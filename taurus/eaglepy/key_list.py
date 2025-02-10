import collections

class Key_List:
    def __init__(self, items=None):
        self.list = collections.OrderedDict()
        if items is not None:
            for i in items:
                self.append(i)

    def append(self, obj):
        """
        Add an object to the end of the list.
        
        :param obj: The object to add.
        """
        self.list[obj.name] = obj

    def extend(self, array):
        for obj in array:
            self.append(obj)
 
    def count(self):
        """
        Returns the number of items in the list.
        
        :returns: The number of items in the list.
        """
        return len(self.list)

    def __iter__(self):
        """
        Returns an iterator for the list values.
        
        :returns: An iterator for the list values.
        """
        return iter(self.list.values())  # Use .values() instead of .itervalues()

    def clear(self):
        """
        Remove all items from the list.
        """
        self.list.clear()

    def __getitem__(self, i):
        """
        Returns the object with the specified key.
        
        :returns: The object with the specified key.
        """
        return self.list[i]

    def names(self):
        """
        Returns a list of the names of all objects in the list, in the order in which they were added.
        
        :returns: A list of the names of all objects in the list.
        """
        return list(self.list.keys())  # Convert keys view to a list

    def items(self):
        """
        Returns a list of all items in the list.
        
        :returns: A list of all items in the list.
        """
        return list(self.list.values())  # Convert values view to a list

    def iternames(self):
        """
        Returns an iterator for the object names.
        
        :returns: An iterator for the object names.
        """
        return iter(self.list.keys())  # Use .keys() instead of .iterkeys()

    def pop(self, name):
        """
        Remove and return the object with the specified name.
        
        :param name: The name of the object to remove.
        
        :returns: The object with the specified name.
        """
        return self.list.pop(name)

    def remove(self, obj):
        """
        Remove the specified object.
        
        :param obj: The object to remove.
        """
        self.list.pop(obj.name)

    def __len__(self):
        """
        Returns the number of items in the list.
        
        :returns: The number of items in the list.
        """
        return len(self.list)

    def has_name(self, name):
        """
        Returns a boolean value indicating whether an object with the
        specified name is contained within the list.
        
        :returns: Whether the list contains an object with the specified name.
        """
        return name in self.list  # Use `in` operator instead of .has_key()

    def item_at_index(self, index):
        """
        Returns the list item at the specified index.
        
        :returns: The list item at the specified index.
        """
        return list(self.list.values())[index]  # Convert values view to a list and access by index