#!/usr/bin/python2.4
#
# Copyright (C) 2009 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import logging
import element

class Annotation(object):
  """Models an annotation on a document.

  Annotations are key/value pairs over a range of content. Annotations
  can be used to store data or to be interpreted by a client when displaying
  the data.
  """

  def __init__(self, name, value, start, end):
    self._name = name
    self._value = value
    self._start = start
    self._end = end

  @property
  def name(self):
    return self._name

  @property
  def value(self):
    return self._value

  @property
  def start(self):
    return self._start

  @property
  def end(self):
    return self._end

  def _shift(self, where, inc):
    """Shift this annotation if it completely or partly stretches where."""
    if self._start > where:
      self._start += inc
    if self._end > where:
      self._end += inc
  
  def serialize(self):
    return {'name': self._name,
            'value': self._value,
            'range': {'start': self._start,
                      'end': self._end}}


class Annotations(object):
  """Models a list of annotations as a dictionary like object on the key."""

  def __init__(self, operation_queue, blip):
    self._operation_queue = operation_queue
    self._blip = blip
    self._store = {}

  def __contains__(self, what):
    if isinstance(what, Annotation):
      what = what.name
    return what in self._store

  def _add_internal(self, name, value, start, end):
    """Internal add annotation does not send out operations."""
    if name in self._store:
      # this could be done more efficient if the list was sorted
      new_list = []
      for existing in self._store[name]:
        if start > existing.end or end < existing.start:
          new_list.append(existing)
        else:
          if existing.value == value:
            # merge the annotations:
            start = min(existing.start, start)
            end = max(existing.end, end)
          else:
            # chop the bits off the existing annotation
            if existing.start < start:
              new_list.append(Annotation(
                  existing.name, existing.value, existing.start, start))
            if existing.end > end:
              new_list.append(Annotation(
                  existing.name, existing.value, existing.end, end))
      new_list.append(Annotation(name, value, start, end))
      self._store[name] = new_list
    else:
      self._store[name] = [Annotation(name, value, start, end)]

  def _delete_internal(self, name, start=0, end=-1):
    if not name in self._store:
      return
    if end < 0:
      end = len(self._blip) + end

    new_list = []
    for a in self._store[name]:
      if start > a.end or end < a.start:
        new_list.append(a)
      elif start < a.start and end > a.end:
        continue
      else:
        if a.start < start:
          new_list.append(Annotation(name, a.value, a.start, start))
        if a.end > end:
          new_list.append(Annotation(name, a.value, a.end, end))
    if new_list:
      self._store[name] = new_list
    else:
      del self._store[name]

  def _shift(self, where, inc):
    for annotations in self._store.values():
      for annotation in annotations:
        annotation._shift(where, inc)

  def __len__(self):
    return len(self._store)

  def __getitem__(self, key):
    return self._store[key]
  
  def serialize(self):
    res = []
    for v in self._store.values():
      res += [a.serialize() for a in v]
    return res


class Blips(object):
  """Class modeling an immutable dictionary of blips."""

  def __init__(self, blips):
    self._blips = blips

  def __getitem__(self, blip_id):
    return self._blips[blip_id]

  def __iter__(self):
    return self._blips.__iter__()

  def __len__(self):
    return len(self._blips)

  def _add(self, ablip):
    self._blips[ablip.blip_id] = ablip

  def _remove_with_id(self, blip_id):
    del self._blips[blip_id]

  def get(self, blip_id, default_value=None):
    return self._blips.get(blip_id, default_value)
  
  def serialize(self):
    res = {}
    for id in self._blips:
      res[id] = self._blips[id].serialize()
    return res


class BlipRefs(object):
  """Represents a set of references to contents in a blip.

  A BlipRefs instance for example can represent the results
  of a search, an explicitly set range, a regular expression
  or refer to the entire blip. BlipRefs are used to express
  operations on a blip in a consistent way that can easily
  be transfered to the server.

  See also Blip.all(..), Blip.first()
  """

  DELETE = 'DELETE'
  REPLACE = 'REPLACE'
  INSERT = 'INSERT'
  INSERT_AFTER = 'INSERT_AFTER'
  ANNOTATE = 'ANNOTATE'
  CLEAR_ANNOTATION = 'CLEAR_ANNOTATION'
  UPDATE_ELEMENT = 'UPDATE_ELEMENT'

  def __init__(self, blip, maxres=1):
    self._blip = blip
    self._maxres = maxres

  @classmethod
  def all(cls, blip, findwhat, maxres=-1, **restrictions):
    """Construct an instance representing the search for text or elements."""
    obj = cls(blip, maxres)
    obj._findwhat = findwhat
    obj._restrictions = restrictions
    obj._hits = lambda: obj._find(findwhat, maxres, **restrictions)
    if findwhat is None:
      # No findWhat, take the entire blip
      obj._params = {}
    else:
      query = {'maxRes': maxres}
      if isinstance(findwhat, basestring):
        query['textMatch'] = findwhat
      else:
        query['elementMatch'] = findwhat.type
        query['restrictions'] = restrictions
      obj._params = {'modifyQuery': query}
    return obj

  @classmethod
  def range(cls, blip, begin, end):
    """Constructs an instance representing an explicitly set range."""
    obj = cls(blip)
    obj._begin = begin
    obj._end = end
    obj._hits = lambda: [(begin, end)]
    obj._params = {'range': {'start': begin, 'end': end}}
    return obj
  
  def _elem_matches(self, elem, clz, **restrictions):
    if not isinstance(elem, clz):
      return False
    for key, val in restrictions.items():
      if getattr(elem, key) != val:
        return False
    return True

  def _find(self, what, maxres=-1, **restrictions):
    """Iterates where 'what' occurs in the blip.

    What can be either a string or a class reference.
    Examples:
        blip.Find('hello') will return the first occurence of the word hello
        blip.Find(element.Gadget, url='http://example.com/gadget.xml')
            will return the first gadget that has as url example.com.

    Args:
      what: what to search for. Can be a class or a string. The class
          should be
      maxres: number of results to return at most, or <= 0 for all.
      restrictions: if what specifies a class, further restrictions
         of the found instances.
    Returns:
      A list of tuples indicating the range of the matches. For a one
      character/element match at position x, (x, x+1) is returned.
    """
    blip = self._blip
    if what is None:
      yield 0, len(blip)
      raise StopIteration
    if isinstance(what, basestring):
      idx = blip._content.find(what)
      count = 0
      while idx != -1:
        yield idx, idx + len(what)
        count += 1
        if count == maxres:
          raise StopIteration
        idx = blip._content.find(what, idx + len(what))
    else:
      count = 0
      for idx, el in blip._elements.items():
        if self._elem_matches(el, what, **restrictions):
          yield idx, idx + 1
          count += 1
          if count == maxres:
            raise StopIteration

  def _execute(self, modify_how, what):
    """Executes this BlipRefs object

    Args:
      modify_how: What to do. Any of the operation declared at the top.
      what: Depending on the operation. For delete, has to be None.
            For the others it is a singleton, a list or a function returning
            what to do; for ANNOTATE tuples of (key, value), for the others
            either string or elements.
            If what is a function, it takes three parameters, the content of
            the blip, the beginning of the matching range and the end.
    """
    blip = self._blip

    if modify_how != BlipRefs.DELETE:
      if type(what) != list:
        what = [what]
      next_index = 0

    matched = []
    # updated_elements is used to store the element type of the
    # element to update
    updated_elements = []

    for start, end in self._hits():
      if start < 0:
        start += len(blip)
        if end == 0:
          end += len(blip)
      if end < 0:
        end += len(blip)
      if len(blip) == 0:
        if start != 0 or end != 0:
          raise IndexError('Start and end have to be 0 for empty document')
      elif start < 0 or end < 1 or start >= len(blip) or end > len(blip):
        raise IndexError('Position outside the document')
      if modify_how == BlipRefs.DELETE:
        for i in range(start, end):
          if i in blip._elements:
            del blip._elements[i]
        blip._shift(end, start - end)
        blip._content = blip._content[:start] + blip._content[end:]
      else:
        if callable(what):
          next = what(blip._content, start, end)
          matched.append(next)
        else:
          next = what[next_index]
          next_index = (next_index + 1) % len(what)
        if isinstance(next, str):
          next = next.decode('utf-8')
        if modify_how == BlipRefs.ANNOTATE:
          key, value = next
          blip.annotations._add_internal(key, value, start, end)
        elif modify_how == BlipRefs.CLEAR_ANNOTATION:
          blip.annotations._delete_internal(next, start, end)
        elif modify_how == BlipRefs.UPDATE_ELEMENT:
          el = blip._elements.get(start)
          if not element:
            raise ValueError('No element found at index %s' % start)
          # the passing around of types this way feels a bit dirty:
          updated_elements.append(element.Element(el.type, properties=next))
          for k, b in next.items():
            setattr(el, k, b)
        else:
          if modify_how == BlipRefs.INSERT:
            end = start
          elif modify_how == BlipRefs.INSERT_AFTER:
            start = end
          elif modify_how == BlipRefs.REPLACE:
            pass
          else:
            raise ValueError('Unexpected modify_how: ' + modify_how)
          if isinstance(next, basestring):
            blip._shift(end, len(next) + start - end)
            blip._content = blip._content[:start] + next + blip._content[end:]
          else:
            blip._shift(end, 1 + start - end)
            blip._content = blip._content[:start] + ' ' + blip._content[end:]
            blip._elements[start] = next

    operation = blip._operation_queue.DocumentModify(blip.wave_id,
                                                     blip.wavelet_id,
                                                     blip.blip_id)
    for param, value in self._params.items():
      operation.set_param(param, value)

    modify_action = {'modifyHow': modify_how}
    if modify_how == BlipRefs.DELETE:
      pass
    elif modify_how == BlipRefs.UPDATE_ELEMENT:
      modify_action['elements'] = updated_elements
    elif (modify_how == BlipRefs.REPLACE or
          modify_how == BlipRefs.INSERT or
          modify_how == BlipRefs.INSERT_AFTER):
      if callable(what):
        what = matched
      if what:
        if isinstance(what[0], basestring):
          modify_action['values'] = what
        else:
          modify_action['elements'] = what
    elif modify_how == BlipRefs.ANNOTATE:
      modify_action['values'] = [x[1] for x in what]
      modify_action['annotationKey'] = what[0][0]
    elif modify_how == BlipRefs.CLEAR_ANNOTATION:
      modify_action['annotationKey'] = what[0]
    operation.set_param('modifyAction', modify_action)

    return self

  def insert(self, what):
    """Inserts what at the matched positions."""
    return self._execute(BlipRefs.INSERT, what)

  def insert_after(self, what):
    """Inserts what just after the matched positions."""
    return self._execute(BlipRefs.INSERT_AFTER, what)

  def replace(self, what):
    """Replaces the matched positions with what."""
    return self._execute(BlipRefs.REPLACE, what)

  def delete(self):
    """Deletes the content at the matched positions."""
    return self._execute(BlipRefs.DELETE, None)

  def annotate(self, name, value=None):
    """Annotates the content at the matched positions.

    You can either specify both name and value to set the
    same annotation, or supply as the first parameter something
    that yields name/value pairs.
    """
    if value is None:
      what = name
    else:
      what = (name, value)
    return self._execute(BlipRefs.ANNOTATE, what)

  def clear_annotation(self, name):
    """Clears the annotation at the matched positions."""
    return self._execute(BlipRefs.CLEAR_ANNOTATION, name)
  
  def update_element(self, new_values):
    """Update an existing element with a set of new values."""
    return self._execute(BlipRefs.UPDATE_ELEMENT, new_values)
  
  def __nonzero__(self):
    """Return whether we have a value."""
    for start, end in self._hits():
      return True
    return False

  def value(self):
    """Convenience method to convert a BlipRefs to value of its first match."""
    for start, end in self._hits():
      if end - start == 1 and start in self._blip._elements:
        return self._blip._elements[start]
      else:
        return self._blip.text[start:end]
    raise ValueError('BlipRefs has no values')
  
  def __getattr__(self, attribute):
    """Mirror the getattr of value().
    
    This allows for clever things like
    first(IMAGE).url
    
    or
    
    blip.annotate_with(key, value).upper()
    """
    return getattr(self.value(), attribute)

  def __radd__(self, other):
    """Make it possible to add this to a string."""
    return other + self.value()
  
  def __cmp__(self, other):
    """Support comparision with target."""
    return cmp(self.value(), other)


class Blip(object):
  """Models a single blip instance.

  Blips are essentially the documents that make up a conversation. Blips can
  live in a hierarchy of blips. A root blip has no parent blip id, but all
  blips have the ids of the wave and wavelet that they are associated with.

  Blips also contain annotations, content and elements, which are accessed via
  the Document object.

  Attributes:
    annotations: List of Annotation objects on this blip.
    blipId: String id of this blip.
    childBlipIds: Set of child blip ids.
    content: Raw text content contained by this blip.
    contributors: Set of contributor ids that have contributed to this blip.
    creator: Participant string id of the creator.
    raw_data: Dictionary of incoming raw JSON data.
    document: Document object for this blip.
    lastModifiedTime: Time that this blip was last modified on the server.
    parentBlipId: String id of the parent blip or None if this is the root.
    waveId: String id of the wave that this blip belongs to.
    waveletId: String id of the wavelet that this belongs to.
  """ 

  def __init__(self, json, other_blips, operation_queue):
    """Inits this blip with JSON data.

    Args:
      json: JSON data dictionary from Wave server.
    """
    self._blip_id = json.get('blipId')
    self._operation_queue = operation_queue
    self._child_blip_ids = set(json.get('childBlipIds', []))
    self._content = json.get('content', '')
    self._contributors = set(json.get('contributors', []))
    self._creator = json.get('creator')
    self._last_modified_time = json.get('lastModifiedTime', 0)
    self._parent_blip_id = json.get('parentBlipId')
    self._wave_id = json.get('waveId')
    self._wavelet_id = json.get('waveletId')
    self._other_blips = Blips(other_blips)
    self._annotations = Annotations(operation_queue, self)
    for annjson in json.get('annotations', []):
      range = annjson['range']
      self._annotations._add_internal(annjson['name'],
                                      annjson['value'],
                                      range['start'],
                                      range['end'])
    self._elements = {}
    json_elements = json.get('elements', {})
    for elem in json_elements:
      self._elements[int(elem)] = element.Element.from_json(json_elements[elem])
    self.raw_data = json

  @property
  def blip_id(self):
    """The id of this blip."""
    return self._blip_id

  @property
  def wave_id(self):
    """The id of the wave that this blip belongs to."""
    return self._wave_id

  @property
  def wavelet_id(self):
    """The id of the wavelet that this blip belongs to."""
    return self._wavelet_id

  @property
  def child_blip_ids(self):
    """The set of the ids of this blip's children."""
    return self._child_blip_ids

  @property
  def child_blips(self):
    """The set of blips that are children of this blip."""
    return set([self._other_blips[id] for id in self._child_blip_ids
                if id in self._other_blips])

  @property
  def contributors(self):
    """The set of participant ids that contributed to this blip."""
    return self._contributors

  @property
  def creator(self):
    """The id of the participant that created this blip."""
    return self._creator

  @property
  def last_modified_time(self):
    """The time in seconds since epoch when this blip was last modified."""
    return self._last_modified_time

  @property
  def parent_blip_id(self):
    """The parent blip_id or None if this is the root blip."""
    return self._parent_blip_id

  @property
  def parent_blip(self):
    """The parent blip or None if it is the root."""
    # if parent_blip_id is None, get will also return None
    return self._other_blips.get(self._parent_blip_id)

  def is_root(self):
    """Returns whether this is the root blip of a wavelet."""
    return self._parent_blip_id is None

  @property
  def annotations(self):
    """The annotations for this document."""
    return self._annotations

  @property
  def elements(self):
    """The elements for this document.

    The elements of a document are things like forms elements, gadgets
    that cannot be expressed as plain text. The elements property of
    a document is a dictionary like object from index in the document
    to element instance. In the text of the document you'll typically
    find a space as a place holder for the element.
    """
    return self._elements.values()

  def __len__(self):
    return len(self._content)

  def __getitem__(self, item):
    """blip[...] returns a BlipRefs of either range or at."""
    if isinstance(item, slice):
      if item.step:
        raise errors.Error('Step not supported for blip slices')
      return self.range(item.start, item.stop)
    else:
      return self.at(item)
  
  def __setitem__(self, item, value):
    """short cut for self.range/at().replace(value)."""
    self.__getitem__(item).replace(value)
    
  def __delitem__(self, item):
    """short cut for self.range/at().delete()."""
    self.__getitem__(item).delete()

  def _shift(self, where, inc):
    """Move element and annotations after where up by inc."""
    new_elements = {}
    for idx, el in self._elements.items():
      if idx >= where:
        idx += inc
      new_elements[idx] = el
    self._elements = new_elements
    self._annotations._shift(where, inc)

  def all(self, findwhat=None, maxres=-1, **restrictions):
    return BlipRefs.all(self, findwhat, maxres, **restrictions)

  def first(self, findwhat=None, **restrictions):
    return BlipRefs.all(self, findwhat, 1, **restrictions)

  def at(self, index):
    return BlipRefs.range(self, index, index + 1)

  def range(self, start, end):
    return BlipRefs.range(self, start, end)
  
  def serialize(self):
    """Return a dictionary representation of this blip ready for json."""
    return {'blipId': self._blip_id,
            'childBlipIds': list(self._child_blip_ids),
            'content': self._content,
            'creator': self._creator,
            'contributors': list(self._contributors),
            'lastModifiedTime': self._last_modified_time,
            'parentBlipId': self._parent_blip_id,
            'waveId': self._wave_id,
            'waveletId': self._wavelet_id,
            'annotations': self._annotations.serialize(),
            'elements': dict([(index, e.serialize())
                              for index, e in self._elements.items()])
          }
  
  def proxy_for(self, proxy_for_id):
    """Return a view on this blip that will proxy for the specified id.
    
    A shallow copy of the current blip is returned with the proxy_for_id
    set. Any modifications made to this copy will be done using the
    proxy_for_id, i.e. the robot+<proxy_for_id>@appspot.com address will
    be used.
    """
    self.wavel
    operation_queue = self._operation_queue().proxy_for(proxy_for_id)
    res = Blip(json={},
               other_blips={},
               operation_queue=operation_queue)
    res._blip_id = self._blip_id
    res._child_blip_ids = self._child_blip_ids
    res._content = self._content
    res._contributors = self._contributors
    res._creator = self._creator
    res._last_modified_time = self._last_modified_time
    res._parent_blip_id = self._parent_blip_id
    res._wave_id = self._wave_id
    res._wavelet_id = self._wavelet_id
    res._other_blips = self._other_blips
    res._annotations = self._annotations
    res._elements = self._elements
    res.raw_data = self.raw_data
    return res

  @property
  def text(self):
    """Returns the raw text content of this document."""
    return self._content

  def find(self, what, **restrictions):
    """Iterate to matching bits of contents.

    Yield either elements or pieces of text.
    """
    br = BlipRefs.all(self, what, **restrictions)
    for start, end in br._hits():
      if end - start == 1 and start in self._elements:
        yield self._elements[start]
      else:
        yield self._content[start:end]
    raise StopIteration

  def append(self, what):
    """Convenience method covering a common pattern."""
    return BlipRefs.all(self, findwhat=None).insert_after(what)

  def reply(self):
    """Create and return a reply to this blip."""
    blip_data = self._operation_queue.BlipCreateChild(self.wave_id,
                                                      self.wavelet_id,
                                                      self.blip_id)
    new_blip = Blip(blip_data, self._other_blips, self._operation_queue)
    self._other_blips._add(new_blip)
    return new_blip

  def append_markup(self, markup):
    """Interpret the markup text as xhtml and append the result to the doc.

    Args:
      markup: The markup'ed text to append.
    """
    self._operation_queue.DocumentAppendMarkup(self.wave_id,
                                               self.wavelet_id,
                                               self.blip_id,
                                               markup)
    #TODO(Douwe): at least strip the html out
    self._content += markup

  def insert_inline_blip(self, position):
    """Inserts an inline blip into this blip at a specific position.

    Args:
      position: Position to insert the blip at.

    Returns:
      The JSON data of the blip that was created.
    """
    blip_data = self._operation_queue.DocumentInlineBlipInsert(
        self.wave_id,
        self.wavelet_id,
        self.blip_id,
        position)
    new_blip = Blip(blip_data, self._other_blips, self._operation_queue)
    self._other_blips._add(new_blip)
    return new_blip