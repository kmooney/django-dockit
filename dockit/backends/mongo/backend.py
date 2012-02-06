from pymongo import Connection
from dockit.backends.base import BaseDocumentStorage, BaseDocumentQuerySet

from django.conf import settings

class DocumentQuery(BaseDocumentQuerySet):
    def __init__(self, collection, doc_class, params=None):
        self.collection = collection
        self.doc_class = doc_class
        self.params = params or list()
        super(DocumentQuery, self).__init__()
    
    @property
    def queryset(self):
        if self.params:
            params = self.params
            if len(params) > 1:
                params = dict(params)
            return self.collection.find(params)
        return self.collection.find()
    
    def wrap(self, entry):
        return self.doc_class.to_python(entry)
    
    def __len__(self):
        return self.queryset.count()
    
    def __nonzero__(self):
        return bool(self.queryset)
    
    def _check_for_operation(self, other):
        if not isinstance(other, DocumentQuery):
            raise TypeError, "operation may only be done against other Document Queries"
        if self.doc_class != other.doc_class:
            raise TypeError, "operation may only be done with the same document type"
    
    def __getitem__(self, val):
        if isinstance(val, slice):
            results = list()
            #TODO i don't think mongo supports passing a slice
            for entry in self.queryset[val]:
                results.append(self.wrap(entry))
            return results
        else:
            return self.wrap(self.queryset[val])
    
    #TODO and & or operations
    def __and__(self, other):
        self._check_for_operation(other)
        cls = type(self)
        params = self.params + other.params
        return cls(self.collection, self.doc_class, params)

class MongoDocumentStorage(BaseDocumentStorage):

    def __init__(self):
        #TODO be proper about this
        self.connection = Connection(settings.MONGO_HOST, settings.MONGO_PORT)
        self.db = self.connection[settings.MONGO_DB]
    
    def save(self, doc_class, collection, data):
        id_field = self.get_id_field_name()
        if data.get(id_field, False) is None:
            del data[id_field]
        if data.get(id_field, False):
            self.db[collection].insert(data)
        else:
            self.db[collection].save(data)
    
    def get(self, doc_class, collection, doc_id):
        from pymongo.objectid import ObjectId
        return self.db[collection].find_one({'_id':ObjectId(doc_id)})
    
    def delete(self, doc_class, collection, doc_id):
        return self.db[collection].remove(doc_id)
    
    def get_id_field_name(self):
        return '_id'
    
    def all(self, doc_class, collection):
        return DocumentQuery(self.db[collection], doc_class)

import indexers

