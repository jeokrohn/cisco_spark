'''
Created on 07.12.2015

@author: jkrohn
'''

class Struct:
    ''' helper class mapping dictionaries to a class
    '''
    def _to_struct(self, obj):
        if isinstance(obj, dict):
            return Struct(obj)
        if isinstance(obj,list):
            return [self._to_struct(v) for v in obj]
        return obj
        
    def __init__(self, d = {}):
        for k,v in d.items():
            self.__dict__[k] = self._to_struct(v)
            
    def get_dict(self):
        return {k:v.get_dict() if isinstance(v, Struct) else v for k,v in self.__dict__.items()}
    
    def __str__(self):
        return str(self.get_dict())
    
    def __contains__(self, key):
        return key in self.__dict__
    
    def __eq__(self, other):
        def eq(v1, v2):
            if v1.__class__.__name__ != v2.__class__.__name__: return False
            if isinstance(v1, list):
                if len(v1)!=len(v2): return False
                for i in range(len(v1)):
                    if not eq(v1[i], v2[i]): return False
                return True
            if isinstance(v1, dict):
                if not eq(sorted(v1.keys()), sorted(v1.keys())): return False
                for k in v1:
                    if v1[k] != v2[k]: return False
                return True
            return v1==v2
            
        if isinstance(other, Struct):
            return eq(self.get_dict(), other.get_dict())
        else:
            return NotImplemented    
    
    def __ne__(self, other):
        if isinstance(other, Struct):
            return not self.__eq__(other)
        else:
            return NotImplemented