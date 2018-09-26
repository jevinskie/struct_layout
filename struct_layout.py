#!/usr/bin/env python

# Copyright (c) 2013, Arvid Norberg
# All rights reserved.
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in
#       the documentation and/or other materials provided with the distribution.
#     * Neither the name of the author nor the names of its
#       contributors may be used to endorse or promote products derived
#       from this software without specific prior written permission.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import sys
import subprocess
import os
from operator import attrgetter
import pprint
import traceback

import beeprint
from IPython.lib.pretty import pprint as ipprint
from nested_lookup import nested_lookup
import pdb

bpc = beeprint.Config()
bpc.text_autoclip_enable = False

mah_item  = {'fields': {'AT_name': 'uint64_t', 'AT_decl_file': '/Applications/Xcode10b5.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/usr/include/_types/_uint64_t.h', 'AT_type': '0x00000bef', 'AT_decl_line': '31'}, 'has_children': False, 'tag': 'TAG_typedef', 'addr': '0x00000580'}

mah_item3 = {'fields': {'AT_name': 'uint64_t', 'AT_decl_file': '/Applications/Xcode10b5.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/usr/include/_types/_uint64_t.h', 'AT_type': '0x00000bef', 'AT_decl_line': '31'}, 'has_children': False, 'tag': 'TAG_typedef', 'addr': '0x00000580'}

def findv(val, dictionary, prepath=()):
	for k, v in dictionary.iteritems():
		path = prepath + (k,)
		if v == val:
			print "k: {} v:{}".format(k, v)
			yield path
		elif isinstance(v, dict):
			for result in findv(val, v, path):
				yield result
		elif isinstance(v, list):
			for idx, d in enumerate(v):
				if d == val:
					print "k: {} v:{}".format(k, d)
					path = path + (idx,)
					yield path
				for result in findv(val, d, path):
					yield result

pointer_size = None

input_file = None
filter_str = ''
profile = None
prof_max = 0

show_standard_types = False
color_output = True
cache_line_size = 64
terminal_width = 80

class DwarfBase:

	def has_fields(self):
		return False

	def size(self):
		return 0

	def match(self, f):
		return False

	def print_struct(self):
		pass

	def full_name(self):
		return ''

class DwarfTypedef(DwarfBase):

	def __init__(self, item, scope, types):
		self._scope = scope
		self._types = types
		if 'AT_type' in item['fields']:
			self._underlying_type = item['fields']['AT_type']
		else:
			# this means "void"
			self._underlying_type = 0

	def size(self):
		return self._types[self._underlying_type].size()

	def name(self):
		if self._underlying_type == 0:
			return 'void'
		else:
			return self._types[self._underlying_type].name()

	def full_name(self):
		# beeprint.pp(self)
		if self._underlying_type == 0:
			return 'void'
		else:
			return self._types[self._underlying_type].full_name()

	def has_fields(self):
		if self._underlying_type == 0: return False
		return self._types[self._underlying_type].has_fields()

	def print_fields(self, offset, expected, indent, prof, cache_lines):
		if self._underlying_type == 0: return 0
		return self._types[self._underlying_type].print_fields(offset, expected, indent, prof, cache_lines)

	def match(self, f):
		if self._underlying_type == 0: return False
		return self._types[self._underlying_type].match(f)

	def print_struct(self):
		if self._underlying_type == 0: return
		self._types[self._underlying_type].print_struct()

class DwarfVoidType(DwarfBase):

	def __init__(self, item, scope, types):
		pass

	def name(self):
		return 'void'

class DwarfConstType(DwarfTypedef):

	def name(self):
		return 'const ' + DwarfTypedef.name(self)

class DwarfVolatileType(DwarfTypedef):

	def name(self):
		return 'volatile ' + DwarfTypedef.name(self)

class DwarfPointerType(DwarfTypedef):

	def size(self):
		global pointer_size
		return pointer_size

	def name(self):
		return DwarfTypedef.name(self) + '*'

	def has_fields(self):
		return False

class DwarfFunPtrType(DwarfBase):

# TODO: support function signatures (for function pointers)

	def __init__(self, item, scope, types):
		self._scope = scope
		pass

	def size(self):
		return 0

	def name(self):
		return '<fun_ptr>'

	def match(self, f): return False

	def has_fields(self):
		return False

class DwarfReferenceType(DwarfTypedef):

	def size(self):
		global pointer_size
		return pointer_size

	def name(self):
		return DwarfTypedef.name(self) + '&'

	def has_fields(self):
		return False

class DwarfRVReferenceType(DwarfReferenceType):

	def name(self):
		return DwarfTypedef.name(self) + '&&'

class DwarfArrayType(DwarfBase):

	def __init__(self, item, scope, types):
		self._scope = scope
		if 'AT_upper_bound' in item['children'][0]['fields']:
			self._num_elements = int(item['children'][0]['fields']['AT_upper_bound'], 16) + 1
		else:
			# this means indeterminate number of items
			# (i.e. basically a regular pointer)
			self._num_elements = -1

		self._underlying_type = item['fields']['AT_type']
		self._types = types

	def size(self):
		return self._types[self._underlying_type].size() * self._num_elements

	def name(self):
		return self._types[self._underlying_type].name() + '[%d]' % self._num_elements

class DwarfBaseType(DwarfBase):

	def __init__(self, item, scope, types):
		self._scope = scope
		if 'AT_name' in item['fields']:
			self._name = item['fields']['AT_name']
		else:
			self._name = '(anonymous)'

		self._size = int(item['fields']['AT_byte_size'], 16)

	def size(self):
		return self._size

	def name(self):
		return self._name

class DwarfEnumType(DwarfBaseType):

	def name(self):
		return 'enum ' + self._name

class DwarfMember:
	def __init__(self, item, types):
		self._types = types
		self._underlying_type = item['fields']['AT_type']
		self._offset = int(item['fields']['AT_data_member_location'], 16)
		if 'AT_name' in item['fields']:
			self._name = item['fields']['AT_name']
		else:
			self._name = '<base-class>'

	def print_field(self, offset, expected, indent, prof, cache_lines):
		t = self._types[self._underlying_type]
		num_padding = (self._offset + offset) - expected
		global color_output
		global prof_max
		global barcolor
		global restore
		global padcolor
		global cachecol

		if prof != None:
			# access profile mode
			if t.has_fields():

				if self._name == '<base-class>': name = '<base-class> %s' % t.name()
				else: name = self._name
				name_field = '%s%s' % ((' ' * indent), name)
				print '      %-91s|' % name_field

				return t.print_fields(self._offset + offset, expected, indent + 1, prof, cache_lines)
			else:

				# a base class with no members. don't waste space by printing it
				if self._name == '<base-class>':
					return self._offset + offset + t.size()

				num_printed = 0
				while len(prof) > 0 and prof[0][0] < self._offset + offset + t.size():
					cnt = prof[0][1]
					member_offset = prof[0][0] - self._offset - offset
					if member_offset != 0: moff = '%+d' % member_offset
					else: moff = ''
					name_field = '%s%s%s' % ((' ' * indent), self._name, moff)
					if len(name_field) > 30: name_field = name_field[:30]

					cache_line = ''
					cache_line_prefix = ''
					if len(cache_lines) == 0 or cache_lines[-1] < (self._offset + offset) / cache_line_size:
						cache_line = '%scache-line %d' % (restore, (self._offset + offset) / cache_line_size)
						cache_line_prefix = cachecol
						cache_lines.append((self._offset + offset) / cache_line_size)

					print '%s%5d %-30s %s%8d: %s%s| %s' % ( \
						cache_line_prefix, \
						self._offset + offset, \
						name_field, \
						barcolor, cnt, \
						print_bar(cnt, prof_max), restore, \
						cache_line)
					num_printed += 1
					del prof[0]
				if num_printed == 0:
					name_field = '%s%s' % ((' ' * indent), self._name)

					cache_line = ''
					cache_line_prefix = ''
					if len(cache_lines) == 0 or cache_lines[-1] < (self._offset + offset) / cache_line_size:
						cache_line = '%scache-line %d' % (restore, (self._offset + offset) / cache_line_size)
						cache_line_prefix = cachecol
						cache_lines.append((self._offset + offset) / cache_line_size)

					print '%s%5d %-91s| %s' % (cache_line_prefix, self._offset + offset, name_field, cache_line)

			return self._offset + offset + t.size()
		else:
			# normal struct layout mode
			if num_padding > 0:
				print '%s   --- %d Bytes padding --- %s%s' % (padcolor, num_padding, (' ' * 60), restore)
				expected = self._offset + offset

			if t.has_fields():
				print '     : %s[%s : %d] %s' % (('  ' * indent), t.name(), t.size(), self._name)
				return t.print_fields(self._offset + offset, expected, indent + 1, prof, cache_lines)
			else:

				cache_line = ''
				cache_line_prefix = ''
				if len(cache_lines) == 0 or cache_lines[-1] < (self._offset + offset) / cache_line_size:
					cache_line = ' -- {cache-line %d}%s' % ((self._offset + offset) / cache_line_size, restore)
					cache_line_prefix = cachecol
					cache_lines.append((self._offset + offset) / cache_line_size)

				l = '%5d: %s[%s : %d] %s' % (self._offset + offset, ('  ' * indent), t.name(), t.size(), self._name)
				print '%s%-*s%s' % (cache_line_prefix, terminal_width - len(cache_line) - 1, l, cache_line)
				return self._offset + offset + t.size()

class DwarfStructType(DwarfBase):

	def __init__(self, item, scope, types):
		self._scope = scope
		self._types = types
		self._declaration = 'AT_declaration' in item['fields']

		if 'AT_declaration' in item['fields']:
			self._size = 0
		else:
			self._size = int(item['fields']['AT_byte_size'], 16)

		if 'AT_name' in item['fields']:
			self._name = item['fields']['AT_name']
		else:
			self._name = '(anonymous)'

		self._fields = []
		if not 'children' in item: return

		try:
			for m in item['children']:
				if m['tag'] != 'TAG_member' \
					and m['tag'] != 'TAG_inheritance': continue
				if not 'AT_data_member_location' in m['fields']:
					continue

				self._fields.append(DwarfMember(m, types))
		except Exception, e:
			print 'EXCEPTION! %s: ' % self._name , e
			traceback.print_exc(e)
			pass

		self._fields = sorted(self._fields, key=attrgetter('_offset'))

	def size(self):
		return self._size

	def name(self):
		return self._name

	def full_name(self):
		return '%s::%s' % (self._scope, self._name)

	def print_struct(self):
		if self._declaration: return

		global structcolor
		global restore
		global padcolor
		global profile

		prof = None
		if profile != None:
			prof_name = '%s::%s' % (self._scope, self._name)
			cnts = profile[prof_name[2:]]
			if cnts != None:
				prof = []
				for k, v in cnts.items():
					# don't show access counters < 1% of max
					if v < prof_max / 100: continue
					prof.append((k, v))
				prof = sorted(prof)

		print '\nstruct %s%s::%s%s [%d Bytes]' % (structcolor, self._scope, self._name, restore, self._size)
		expected = self.print_fields(0, 0, 0, prof, [])

		if profile == None:
			num_padding = (self._size) - expected
			if num_padding > 0:
				print '%s   --- %d Bytes padding --- %s%s' % (padcolor, num_padding, (' ' * 60), restore)

	def print_fields(self, offset, expected, indent, prof, cache_lines):
		for f in self._fields:
			expected = max(expected, f.print_field(offset, expected, indent, prof, cache_lines))
		return expected

	def has_fields(self):
		if len(self._fields) > 0: return True
		else: return False

	def match(self, f):
		if self._declaration: return False

		typename = '%s::%s' % (self._scope, self._name)

		global profile
		if profile != None:
			# strip the :: prefix to match the names in the profile
			name = typename[2:]
			return name in profile

		global show_standard_types
		if not show_standard_types:
			if typename.startswith('::std::'): return False
			if typename.startswith('::__gnu_cxx::'): return False
			if typename.startswith('::__'): return False
		if len(f) == 0: return True
		return typename.startswith(f)

class DwarfUnionType(DwarfStructType):

	def name(self):
		return 'union ' + DwarfStructType.name(self)

	def print_struct(self):
		print '\nunion %s::%s [%d Bytes]' % (self._scope, self._name, self._size)
		self.print_fields(0, 0, 0, None, [])

class DwarfMemberPtrType(DwarfTypedef):

	def __init__(self, item, scope, types):
		DwarfTypedef.__init__(self, item, scope, types)
		self._class_type = item['fields']['AT_containing_type']

	def size(self):
		global pointer_size
		return pointer_size

	def name(self):
		return '%s (%s::*)' % (self._types[self._underlying_type].name(), self._types[self._class_type].name())

	def match(self, f): return False

tag_to_type = {
	'TAG_base_type': DwarfBaseType,
	'TAG_pointer_type': DwarfPointerType,
	'TAG_reference_type': DwarfReferenceType,
	'TAG_rvalue_reference_type': DwarfRVReferenceType,
	'TAG_typedef': DwarfTypedef,
	'TAG_array_type': DwarfArrayType,
	'TAG_const_type': DwarfConstType,
	'TAG_volatile_type': DwarfVolatileType,
	'TAG_structure_type': DwarfStructType,
	'TAG_class_type': DwarfStructType,
	'TAG_ptr_to_member_type': DwarfMemberPtrType,
	'TAG_enumeration_type': DwarfEnumType,
	'TAG_subroutine_type': DwarfFunPtrType,
	'TAG_union_type': DwarfUnionType,
	'TAG_unspecified_type': DwarfVoidType,
}

def parse_tag(lno, lines):
	fields = {}

	l = lines[lno].strip()
	lno += 1

	if not l.startswith('0x'): return (lno, None)

	try:
		addr, tag = l.split(':', 1)
		tag = tag.strip().split(' ')[0]
	except:
		return (lno, None)

	has_children = l.endswith('*')

	while lno < len(lines) and lines[lno].strip() != '':
		l = lines[lno].strip()
		lno += 1
		try:
			key, value = l.split('(', 1)
			value = value.strip().split(')',1)[0].strip()
		except:
			continue

		if len(value) > 0 and value[0] == '"' and value[-1] == '"':
			value = value[1:-1]

		# values that have {...} in them, pick out the
		# content of the brackets
		if len(value) > 0 and value[0] == '{':
			value = value.split('}')[0][1:]
		fields[key] = value

	return (lno, {'fields': fields, 'tag': tag, 'addr': addr, 'has_children': has_children})

def parse_recursive(lno, lines):

	# skip blank lines
	while lno < len(lines):
		l = lines[lno].strip()
		if l.startswith('0x'): break
		lno += 1
	if lno == len(lines): return lno, None

	lno, item = parse_tag(lno, lines)
	if item == None: return lno, None
	# if item['addr'] == '0x00000580':
	# 	pdb.set_trace()
	# 	pass

	children = []
	if not item['has_children']:
		return lno, item

	while lno < len(lines):
		lno, i = parse_recursive(lno, lines)
		if i == None: break
		if i['tag'] == 'NULL': break
		children.append(i)

	item['children'] = children
	return lno, item

def collect_types(tree, scope, types, typedefs):

	# if 'AT_name' in tree['fields'] and tree['fields']['AT_name'] == 'uint64_t':
	# 	pdb.set_trace()
	# 	pass

	if 'AT_name' in tree['fields']:
		inner_scope = scope + '::' + tree['fields']['AT_name']
	else:
		inner_scope = scope + '::' + '(anonymous)'

	if tree['tag'] in tag_to_type:

		declaration = 'AT_declaration' in tree['fields']

		# this is necessary. For some reason, the base class reference
		# can sometimes refer to a declaration of the subclass instead
		# of the definition of it, even when the definition is available.
		# this simply replaces all declarations with the definition if
		# the definition has been seen.
		if declaration and inner_scope in typedefs and \
			'AT_name' in tree['fields'] and \
			'def' in typedefs[inner_scope]:
			# use an existing (fully defined) object instead of
			# creating another declaration object
			obj = typedefs[inner_scope]['def']
		else:
			# construct a new object
			obj = tag_to_type[tree['tag']](tree, scope, types)

			if not inner_scope in typedefs: typedefs[inner_scope] = {}

			# if this is a complete type and we've previously seen a
			# declaration, update the declaration to this
			if not declaration:
				if inner_scope in typedefs:
					if 'decl' in typedefs[inner_scope]:
						for decl in typedefs[inner_scope]['decl']:
							types[decl] = obj
				typedefs[inner_scope]['def'] = obj
			else:
				# add this to the declaration list
				if not 'decl' in typedefs[inner_scope]:
					typedefs[inner_scope]['decl'] = [tree['addr']]
				else:
					typedefs[inner_scope]['decl'].append(tree['addr'])

		types[tree['addr']] = obj

	if tree['tag'] == 'TAG_namespace' \
		or tree['tag'] == 'TAG_structure_type' \
		or tree['tag'] == 'TAG_class_type' \
		or tree['tag'] == 'TAG_union_type':

		if 'children' in tree:
			for c in tree['children']:
				collect_types(c, inner_scope, types, typedefs)
	
	elif tree['tag'] == 'TAG_compile_unit' \
		or tree['tag'] == 'TAG_subprogram' \
		or tree['tag'] == 'TAG_module':
		if 'children' in tree:
			for c in tree['children']:
				collect_types(c, scope, types, typedefs)

def print_bar(val, maximum):

	width = 50

	# blocks from empty to full (left to right)
	blocks = [
		u' ', u'\u258F', u'\u258E', u'\u258D', u'\u258C' \
		, u'\u258B', u'\u258A', u'\u2589', u'\u2588']

	s = u''

	num_blocks = val * width / float(maximum)
	while num_blocks > 1.0:
		s += blocks[8]
		num_blocks -= 1.0

	s += blocks[int(num_blocks * 8)]

	s += u' ' * (width - len(s))

	return s.encode('utf-8')

def get_terminal_size():
    import os
    env = os.environ
    def ioctl_GWINSZ(fd):
        try:
            import fcntl, termios, struct, os
            cr = struct.unpack('hh', fcntl.ioctl(fd, termios.TIOCGWINSZ,
        '1234'))
        except:
            return
        return cr
    cr = ioctl_GWINSZ(0) or ioctl_GWINSZ(1) or ioctl_GWINSZ(2)
    if not cr:
        try:
            fd = os.open(os.ctermid(), os.O_RDONLY)
            cr = ioctl_GWINSZ(fd)
            os.close(fd)
        except:
            pass
    if not cr:
        cr = (env.get('LINES', 25), env.get('COLUMNS', 80))
    return int(cr[1]), int(cr[0])

def print_usage():
	print 'usage: %s [options] exe-file [name-prefix-filter]\n' % sys.argv[0]
	print 'exe-file must have DWARF debug symbols in it. It'
	print 'may be an object file, shared library or executable. On Mac'
	print 'dsymutils will be invoked for files with no direct debug symbols'
	print 'in them.'
	print ''
	print 'name-prefix-filter is an optional argument. When'
	print 'specified, only types whose prefix matches this are'
	print 'printed. Names are fully qualified and start with ::'
	print 'to denote the global scope.'
	print ''
	print 'OPTIONS'
	print '-a           print all types, including standard library'
	print '             and implementation detail types'
	print '-c           disable color output'
	print '-p <file>    use the specified access_profile output file'
	print '             to display use counts for only instrumented types'
	print ''
	print 'the dwarfdump tool is a dependency and need to be'
	print 'installed on your system. On Mac OS X you may need dsymutil'
	print 'in order to link debug symbols together'
	sys.exit(1)

def process_dwarf_file(input_file):
	global pointer_size

	f = subprocess.Popen(['dwarfdump', input_file], stdout=subprocess.PIPE)

	# types maps addresses to types
	types = {}

	# typedefs maps fully qualiied names of
	# types to their address, but only complete
	# types, not declarations. This is used to rewrite
	# links to declarations to definitions when available
	typedefs = {}

	lines = []

	# TODO: it would probably be a lot faster to change the
	# parser to just use the file object instead of reading
	# the whole file up-front

	for l in f.stdout:
		lines.append(l)

	lno = 0
	items = []

	while lno < len(lines):
		l = lines[lno]
		lno += 1
		if 'Compile Unit:' in l and 'addr_size =' in l:
			pointer_size = int(l.split('addr_size =')[1].strip().split(' ', 1)[0], 16)
			break

	if pointer_size == None:
		return False

	while lno < len(lines):
		lno, tree = parse_recursive(lno, lines)
		if tree != None:
			items.append(tree)
	
	# print 'ITEMZ'
	# beeprint.pp(items, max_depth=99999999999, width=9999999999, sort_keys=False, config=bpc)
	# pprint.pprint(items)
	for i in items:
		collect_types(i, '', types, typedefs)
		# if False and list(findv(mah_item, i)) != []:
		# 	print 'MAH TYPES'
		# 	pprint.pprint(types)
		# 	print 'MAH TYPEDEFS'
		# 	pprint.pprint(typedefs)
		# 	print 'MAH DONENESS'

	already_printed = set()

	for a,t in types.items():
		if t.full_name() in already_printed: continue
		if not t.match(filter_str): continue
		t.print_struct()
		already_printed.add(t.full_name())

	return True

def parse_profile(it):

	global prof_max
	ret = {}
	for l in it:
		if l.strip() == '': break

		if not l.startswith('   '):
			print 'incorrect profiler file format'
			sys.exit(1)
		offset, count = l.strip().split(':')
		offset = int(offset)
		count = int(count)
		if count > prof_max:
			prof_max = count

		ret[offset] = count
	return ret

(terminal_width, h) = get_terminal_size()

# parse command line arguments
i = 1

while i < len(sys.argv):
	a = sys.argv[i]
	if a == '-a': show_standard_types = True
	elif a == '-c': color_output = False
	elif a == '-p':
		i += 1
		profile_file = sys.argv[i]
		f = open(profile_file, 'r')
		profile = {}
		it = iter(f)
		print it.next() # skip the first blank line
		for l in it:
			name = l.strip()
			profile[name] = parse_profile(it)
		f.close()
	else: break
	i += 1

if len(sys.argv) <= i:
	print_usage()

input_file = sys.argv[i]
i += 1
	
if len(sys.argv) > i:
	filter_str = sys.argv[i]
	i += 1

# set up these global variables controlling
# colors of different components
if color_output:
	barcolor = '\x1b[33m'
	restore = '\x1b[0m'
	padcolor = '\x1b[41m'
	structcolor = '\x1b[1m'
	cachecol = '\x1b[44m'
else:
	barcolor = ''
	restore = ''
	padcolor = ''
	structcolor = ''
	cachecol = ''

# if it fails, it may be because we're on Mac OS and
# trying to read debug symbols from an executable
if not process_dwarf_file(input_file):
	dwarf_file = input_file + '.dwarf'
	if not os.path.exists(dwarf_file) \
		or os.stat(input_file).st_mtime > os.stat(dwarf_file).st_mtime:
		subprocess.call(['dsymutil', '--flat', input_file]);
	process_dwarf_file(dwarf_file)

