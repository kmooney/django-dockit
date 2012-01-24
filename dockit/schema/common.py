from exceptions import DotPathNotFound

SCHEMAS = dict()

def register_schema(key, cls):
    SCHEMAS[key] = cls

def get_schema(key):
    return SCHEMAS[key]

class DotPathTraverser(object):
    def __init__(self, dotpath):
        self.dotpath = dotpath
        self.remaining_paths = dotpath.split('.')
        self.resolved_paths = list() #part, value, field
    
    def resolve_for_schema(self, schema):
        from fields import SchemaField
        entry = {'value':None,
                 'field':SchemaField(schema=schema),
                 'part':None,}
        self.resolved_paths = [entry]
        self._resolve_loop()
    
    def resolve_for_instance(self, instance):
        from fields import SchemaField
        entry = {'value':instance,
                 'field':SchemaField(schema=type(instance)),
                 'part':None,}
        self.resolved_paths = [entry]
        self._resolve_loop()
    
    def _resolve_loop(self):
        while self.remaining_paths:
            count = len(self.remaining_paths)
            self.resolve_next()
            if count == len(self.remaining_paths):
                #TODO raise error, the remaining_path did not expire
                assert False
    
    def resolve_next(self):
        current = self.current
        if current['field'] is None:
            if hasattr(current['value'], 'traverse_dot_path'):
                current['value'].traverse_dot_path(self)
                #note the result may or may not have a field
            else:
                print current
                assert False
                #raise DotPathNotFound
        else:
            current['field'].traverse_dot_path(self)
    
    @property
    def next_part(self):
        return self.remaining_paths[0]
    
    @property
    def current(self):
        return self.resolved_paths[-1]
    
    @property
    def current_value(self):
        return self.current['value']
    
    def end(self, field=None, value=None):
        last_entry = self.resolved_paths[-1]
        if field:
            last_entry['field'] = field
        if value:
            last_entry['value'] = value
    
    def next(self, field=None, value=None):
        part = self.remaining_paths.pop(0)
        if value and field is None:
            from fields import SchemaField
            from schema import Schema
            if isinstance(value, Schema):
                field = SchemaField(schema=type(value))
        entry = {'field':field,
                 'value':value,
                 'part':part,}
        self.resolved_paths.append(entry)
    
    def set_value(self, value):
        parent_entry = self.resolved_paths[-2]
        part = self.current['part']
        if parent_entry['field']:
            parent_entry['field'].set_value(parent_entry['value'], part, value)
        elif parent_entry['value']:
            parent_entry['value'].set_value(part, value)
        else:
            assert False

class DotPathList(list):
    def traverse_dot_path(self, traverser):
        if traverser.remaining_paths:
            new_value = None
            name = traverser.next_part
            try:
                new_value = self[int(name)]
            except ValueError:
                raise DotPathNotFound("Invalid index given, must be an integer")
            except IndexError:
                pass
            traverser.next(value=new_value)
        else:
            traverser.end(value=self)
    
    def set_value(self, attr, value):
        index = int(attr)
        if index == len(self):
            self.append(value)
        else:
            self[index] = value

class DotPathDict(dict):
    def traverse_dot_path(self, traverser):
        if traverser.remaining_paths:
            new_value = None
            name = traverser.next_part
            try:
                new_value = self[name]
            except KeyError:
                pass
            traverser.next(value=new_value)
        else:
            traverser.end(value=self)
    
    def set_value(self, attr, value):
        self[attr] = value

