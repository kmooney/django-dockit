import sys
import uuid

from django.utils.encoding import force_unicode
from django.utils.datastructures import SortedDict
from django.db.models import FieldDoesNotExist
from django.core.exceptions import ObjectDoesNotExist, MultipleObjectsReturned

from manager import Manager
from loading import register_documents
from common import DotPathTraverser, UnSet
from signals import pre_save, post_save, pre_delete, post_delete, class_prepared, pre_init, post_init
import options

def subclass_exception(name, parents, module):
    return type(name, parents, {'__module__': module})

class SchemaBase(type):
    """
    Metaclass for all schemas.
    """
    options_module = options.SchemaOptions
    
    def __new__(cls, name, bases, attrs):
        super_new = super(SchemaBase, cls).__new__
        parents = [b for b in bases if isinstance(b, SchemaBase)]
        options_module = cls.options_module
        
        module = attrs.pop('__module__')
        new_class = super_new(cls, name, bases, {'__module__': module})
        
        attr_meta = attrs.pop('Meta', None)
        if not attr_meta:
            meta = getattr(new_class, 'Meta', None)
        else:
            meta = attr_meta
            #TODO collect meta from parents
            if getattr(meta, 'proxy', False) or getattr(meta, 'typed_key', False):
                if not hasattr(new_class, '_meta'):
                    raise ValueError('Proxy schemas must inherit from another schema')
                parent_meta = getattr(new_class, '_meta')
                for key in options_module.DEFAULT_NAMES:
                    if not hasattr(meta, key) and hasattr(parent_meta, key):
                        setattr(meta, key, getattr(parent_meta, key))
        
        if getattr(meta, 'app_label', None) is None:
            document_module = sys.modules[new_class.__module__]
            parts = document_module.__name__.split('.')
            #dockitcms.models.submodule.MyModel => applabel=dockitcms, objectname=MyModel
            if 'models' in parts:
                index = parts.index('models')
                app_label = parts[index-1]
            else:
                app_label = parts[-2]
        else:
            app_label = getattr(meta, 'app_label')
        
        parent_fields = list()
        for base in bases:
            if hasattr(base, '_meta') and hasattr(base._meta, 'fields'):
                parent_fields.append(base._meta.fields)
        
        fields = [(field_name, attrs.pop(field_name)) for field_name, obj in attrs.items() if hasattr(obj, 'creation_counter')]
        fields.sort(key=lambda x: x[1].creation_counter)
        
        options = options_module(meta, app_label=app_label, parent_fields=parent_fields)
        options.process_values(new_class)
        setattr(new_class, '_meta', options)
        
        for field_name, obj in fields:
            new_class.add_to_class(field_name, obj)
        
        for obj_name, obj in attrs.items():
            new_class.add_to_class(obj_name, obj)
        
        new_class.add_to_class('_meta', options)
        
        class_prepared.send(**{'sender':cls, 'class':new_class})
        return new_class
    
    def add_to_class(cls, name, value):
        if hasattr(value, 'contribute_to_class'):
            try:
                value.contribute_to_class(cls, name)
            except TypeError:
                setattr(cls, name, value)
        else:
            setattr(cls, name, value)

class Schema(object):
    __metaclass__ = SchemaBase
    
    def __init__(self, **kwargs):
        pre_init.send(sender=self.__class__, kwargs=kwargs)
        #super(Schema, self).__init-_()
        self._primitive_data = dict()
        self._python_data = dict()
        self._parent = None #TODO make parent a configurable field
        for key, value in kwargs.iteritems():
            if key in self._meta.fields or key in ('_primitive_data', '_python_data', '_parent'):
                setattr(self, key, value)
            else:
                self[key] = value
        assert isinstance(self._primitive_data, dict), str(type(self._primitive_data))
        assert isinstance(self._python_data, dict), str(type(self._python_data))
        if self._meta.typed_field and self._meta.typed_key:
            self[self._meta.typed_field] = self._meta.typed_key
        post_init.send(sender=self.__class__, instance=self)
    
    @classmethod
    def to_primitive(cls, val):
        #CONSIDER shouldn't val be a schema?
        if cls._meta.typed_field and cls._meta.typed_key:
            val[cls._meta.typed_field] = cls._meta.typed_key
        if hasattr(val, '_primitive_data') and hasattr(val, '_python_data') and hasattr(val, '_meta'):
            #we've cached python values on access, we need to pump these back to the primitive dictionary
            for name, entry in val._python_data.iteritems():
                if name in val._meta.fields:
                    try:
                        val._primitive_data[name] = val._meta.fields[name].to_primitive(entry)
                    except:
                        print name, val._meta.fields[name], entry
                        raise
                else:
                    #TODO run entry through generic primitive processor
                    val._primitive_data[name] = entry
            return val._primitive_data
        assert isinstance(val, (dict, list, type(None))), str(type(val))
        return val
    
    @classmethod
    def to_portable_primitive(cls, val):
        #CONSIDER shouldn't val be a schema?
        if cls._meta.typed_field and cls._meta.typed_key:
            val[cls._meta.typed_field] = cls._meta.typed_key
        if hasattr(val, '_primitive_data') and hasattr(val, '_python_data') and hasattr(val, '_meta'):
            data = dict(val._primitive_data)
            for name in val.keys():
                try:
                    entry = val[name]
                except KeyError: #TODO this should not happen
                    continue
                if name in val._meta.fields:
                    try:
                        data[name] = val._meta.fields[name].to_portable_primitive(entry)
                    except:
                        print name, val._meta.fields[name], entry
                        raise
                else:
                    #TODO run entry through generic primitive processor
                    data[name] = entry
            #TODO proces any primitive_data
            return data
        assert isinstance(val, (dict, list, type(None))), str(type(val))
        return val
    
    @classmethod
    def to_python(cls, val, parent=None):
        if val is None:
            val = dict()
        if cls._meta.typed_field:
            field = cls._meta.fields[cls._meta.typed_field]
            key = val.get(cls._meta.typed_field, None)
            if key:
                try:
                    cls = field.schemas[key]
                except KeyError:
                    #TODO emit a warning
                    pass
        return cls(_primitive_data=val, _parent=parent)
    
    def normalize_portable_primitives(self):
        changed = False
        for key, field in self._meta.fields.iteritems():
            if key in self._primitive_data:
                entry = self._primitive_data[key]
                new_entry = field.normalize_portable_primitives(entry)
                if entry != new_entry:
                    changed = True
                    self._primitive_data[key] = new_entry
        return changed
    
    def __getattribute__(self, name):
        fields = object.__getattribute__(self, '_meta').fields
        if name in fields:
            try:
                python_data = object.__getattribute__(self, '_python_data')
            except AttributeError:
                return object.__getattribute__(self, name)
            if name not in python_data:
                primitive_data = object.__getattribute__(self, '_primitive_data')
                python_data[name] = fields[name].to_python(primitive_data.get(name), parent=self)
            return python_data[name]
        return object.__getattribute__(self, name)
    
    def __setattr__(self, name, val):
        if name in self._meta.fields:
            field = self._meta.fields[name]
            if not field.is_instance(val):
                val = field.to_python(val)
            self._python_data[name] = val
            #field = self._fields[name]
            #store_val = field.to_primitive(val)
            #self._primtive_data[name] = store_val
        else:
            super(Schema, self).__setattr__(name, val)
    
    def __getitem__(self, key):
        assert isinstance(key, basestring)
        if key in self._meta.fields:
            return getattr(self, key)
        if key in self._primitive_data and key not in self._python_data:
            from serializer import PRIMITIVE_PROCESSOR
            r_val = self._primitive_data[key]
            p_val = PRIMITIVE_PROCESSOR.to_python(r_val)
            self._python_data[key] = p_val
        return self._python_data[key]
    
    def __setitem__(self, key, value):
        if key in self._meta.fields:
            setattr(self, key, value)
            return
        self._python_data[key] = value
    
    def __delitem__(self, key):
        if key in self._meta.fields:
            setattr(self, key, None)
            return
        self._python_data.pop(key, None)
        self._primitive_data.pop(key, None)
    
    def __hasitem__(self, key):
        if key in self._meta.fields:
            return True
        return key in self._python_data
    
    def keys(self):
        #TODO more dictionary like functionality
        return set(self._python_data.keys() + self._meta.fields.keys())
    
    def traverse_dot_path(self, traverser):
        if traverser.remaining_paths:
            value = field = None
            name = traverser.next_part
            try:
                value = self[name]
            except KeyError:
                pass
            if name in self._meta.fields:
                field = self._meta.fields[name]
            traverser.next(value=value, field=field)
        else:
            traverser.end(value=self)
    
    def dot_notation(self, notation):
        return self.dot_notation_to_value(notation)
    
    def dot_notation_set_value(self, notation, value):
        traverser = DotPathTraverser(notation)
        traverser.resolve_for_instance(self)
        
        try:
            traverser.set_value(value)
        except:
            print traverser.resolved_paths
            raise
    
    def dot_notation_to_value(self, notation):
        traverser = DotPathTraverser(notation)
        traverser.resolve_for_instance(self)
        return traverser.current['value']
    
    def dot_notation_to_field(self, notation):
        traverser = DotPathTraverser(notation)
        traverser.resolve_for_instance(self)
        return traverser.current['field']
    
    def set_value(self, attr, value):
        if value is UnSet:
            del self[attr]
        else:
            self[attr] = value
    
    def serializable_value(self, field_name):
        try:
            field = self._meta.get_field_by_name(field_name)[0]
        except FieldDoesNotExist:
            return getattr(self, field_name)
        return getattr(self, field.attname)
    
    def __str__(self):
        if hasattr(self, '__unicode__'):
            return force_unicode(self).encode('utf-8')
        return '%s object' % self.__class__.__name__
    
    def __eq__(self, other):
        return isinstance(other, self.__class__) and self.to_primitive(self) == other.to_primitive(other)
    
    def __ne__(self, other):
        return not self.__eq__(other)
    
    def __hash__(self):
        return hash(self.to_primitive(self))

class DocumentBase(SchemaBase):
    options_module = options.DocumentOptions
    
    def __new__(cls, name, bases, attrs):
        new_class = SchemaBase.__new__(cls, name, bases, attrs)
        if 'objects' not in attrs:
            objects = Manager()
            objects.contribute_to_class(new_class, 'objects')
        
        parents = [b for b in bases if isinstance(b, DocumentBase)]
        
        module = new_class.__module__
        
        if not new_class._meta.virtual:
            new_class.add_to_class('DoesNotExist', subclass_exception('DoesNotExist',
                    tuple(x.DoesNotExist
                            for x in parents if hasattr(x, '_meta') and not x._meta.virtual)
                                    or (ObjectDoesNotExist,), module))
            new_class.add_to_class('MultipleObjectsReturned', subclass_exception('MultipleObjectsReturned',
                    tuple(x.MultipleObjectsReturned
                            for x in parents if hasattr(x, '_meta') and not x._meta.virtual)
                                    or (MultipleObjectsReturned,), module))
        if parents and new_class._meta.proxy:
            new_class._meta.module_name = parents[0]._meta.module_name
        
        #ensure index on natural key hash
        if not new_class._meta.virtual and not new_class._meta.proxy:
            #TODO these fields should be definable at the document level
            from fields import CharField, DictField
            new_class.add_to_class('@natural_key_hash', CharField(editable=False, null=False))
            new_class.add_to_class('@natural_key', DictField(editable=False, null=False))
            
            register_documents(new_class._meta.app_label, new_class)
            
            new_class.objects.index('@natural_key_hash__exact').commit()
        return new_class

class Document(Schema):
    __metaclass__ = DocumentBase
    
    def get_id(self):
        backend = self._meta.get_backend()
        return str(backend.get_id(self._primitive_data))
    
    def _get_pk_val(self):
        return self.get_id()
    
    pk = property(get_id)
    
    def get_or_create_natural_key(self):
        if '@natural_key' not in self._primitive_data:
            self._primitive_data['@natural_key'] = self.create_natural_key()
        if '@natural_key_hash' not in self._primitive_data:
            self._primitive_data['@natural_key_hash'] = self._get_natural_key_hash(self._primitive_data['@natural_key'])
        return self._primitive_data['@natural_key']
    
    def create_natural_key(self):
        '''
        Documents may want to override this to return a dictionary of values representing the natural key of the document.
        Other applications may want the natural key to be based off fields in the document rather then a UUID.
        '''
        return {'uuid': uuid.uuid4().hex}
    
    @property
    def natural_key(self):
        return self.get_or_create_natural_key()
    
    @property
    def natural_key_hash(self):
        self.get_or_create_natural_key()
        return self._primitive_data['@natural_key_hash']
    
    def _get_natural_key_hash(self, nkey):
        vals = tuple(nkey.items())
        return str(hash(vals)) #TODO convert to hex value
    
    @classmethod
    def to_primitive(cls, val):
        val.get_or_create_natural_key()
        ret = Schema.to_primitive(val)
        return ret
    
    @classmethod
    def to_portable_primitive(cls, val):
        val.get_or_create_natural_key()
        ret = Schema.to_portable_primitive(val)
        return ret
    
    def save(self):
        from dockit.backends import get_index_router
        created = not self.pk
        pre_save.send(sender=type(self), instance=self)
        backend = self._meta.get_document_backend_for_write()
        data = type(self).to_primitive(self)
        backend.save(type(self), self._meta.collection, data)
        get_index_router().on_save(type(self), self._meta.collection, self.get_id(), data)
        post_save.send(sender=type(self), instance=self, created=created)
    
    def delete(self):
        from dockit.backends import get_index_router
        pre_delete.send(sender=type(self), instance=self)
        backend = self._meta.get_document_backend_for_write()
        backend.delete(type(self), self._meta.collection, self.get_id())
        get_index_router().on_delete(type(self), self._meta.collection, self.get_id())
        post_delete.send(sender=type(self), instance=self)
    
    def serializable_value(self, field_name):
        try:
            field = self._meta.get_field_by_name(field_name)[0]
        except FieldDoesNotExist:
            return getattr(self, field_name)
        return getattr(self, field.attname)
    
    def __eq__(self, other):
        return isinstance(other, self.__class__) and self.pk == other.pk
    
    def __hash__(self):
        return hash((self._meta.collection, self.pk))

class UserMeta(object):
    def __init__(self, **kwargs):
        for key, value in kwargs.iteritems():
            setattr(self, key, value)

def create_schema(name, fields, attrs={}, module='dockit.models', base=SchemaBase, parents=(Schema,), **kwargs):
    all_attrs = SortedDict(fields)
    all_attrs.update(attrs)
    all_attrs['__module__'] = module
    if kwargs:
        all_attrs['Meta'] = UserMeta(**kwargs)
    return base.__new__(base, name, parents, all_attrs)

def create_document(name, fields, attrs={}, module='dockit.models', base=DocumentBase, parents=(Document,), **kwargs):
    all_attrs = SortedDict(fields)
    all_attrs.update(attrs)
    all_attrs['__module__'] = module
    if kwargs:
        all_attrs['Meta'] = UserMeta(**kwargs)
    return base.__new__(base, name, parents, all_attrs)

