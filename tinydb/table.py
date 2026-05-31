"""
This module implements tables, the central place for accessing and manipulating
data in TinyDB.
"""

from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import (
    NoReturn,
    Optional,
    Union,
    cast,
    overload
)

from .queries import QueryLike
from .storages import Storage
from .utils import LRUCache

__all__ = ('Document', 'Table')


class Document(dict):
    """
    A document stored in the database.

    This class provides a way to access both a document's content and
    its ID using ``doc.doc_id``.
    """

    def __init__(self, value: Mapping, doc_id: int):
        super().__init__(value)
        self.doc_id = doc_id


class Table:
    """
    Represents a single TinyDB table.

    It provides methods for accessing and manipulating documents.

    .. admonition:: Query Cache

        As an optimization, a query cache is implemented using a
        :class:`~tinydb.utils.LRUCache`. This class mimics the interface of
        a normal ``dict``, but starts to remove the least-recently used entries
        once a threshold is reached.

        The query cache is updated on every search operation. When writing
        data, the whole cache is discarded as the query results may have
        changed.

    .. admonition:: Customization

        For customization, the following class variables can be set:

        - ``document_class`` defines the class that is used to represent
          documents,
        - ``document_id_class`` defines the class that is used to represent
          document IDs,
        - ``query_cache_class`` defines the class that is used for the query
          cache
        - ``default_query_cache_capacity`` defines the default capacity of
          the query cache

        .. versionadded:: 4.0


    :param storage: The storage instance to use for this table
    :param name: The table name
    :param cache_size: Maximum capacity of query cache
    :param persist_empty: Store new table even with no operations on it
    """

    #: The class used to represent documents
    #:
    #: .. versionadded:: 4.0
    document_class = Document

    #: The class used to represent a document ID
    #:
    #: .. versionadded:: 4.0
    document_id_class = int

    #: The class used for caching query results
    #:
    #: .. versionadded:: 4.0
    query_cache_class = LRUCache

    #: The default capacity of the query cache
    #:
    #: .. versionadded:: 4.0
    default_query_cache_capacity = 10

    def __init__(
        self,
        storage: Storage,
        name: str,
        cache_size: int = default_query_cache_capacity,
        persist_empty: bool = False
    ):
        """
        Create a table instance.
        """

        self._storage = storage
        self._name = name
        self._query_cache: LRUCache[QueryLike, list[Document]] \
            = self.query_cache_class(capacity=cache_size)

        self._next_id = None
        if persist_empty:
            self._update_table(lambda table: table.clear())

    def __repr__(self):
        args = [
            'name={!r}'.format(self.name),
            'total={}'.format(len(self)),
            'storage={}'.format(self._storage),
        ]

        return '<{} {}>'.format(type(self).__name__, ', '.join(args))

    @property
    def name(self) -> str:
        """
        Get the table name.
        """
        pass

    @property
    def storage(self) -> Storage:
        """
        Get the table storage instance.
        """
        pass

    def insert(self, document: Mapping) -> int:
        """
        Insert a new document into the table.

        :param document: the document to insert
        :returns: the inserted document's ID
        """
        pass

    def insert_multiple(self, documents: Iterable[Mapping]) -> list[int]:
        """
        Insert multiple documents into the table.

        :param documents: an Iterable of documents to insert
        :returns: a list containing the inserted documents' IDs
        """
        pass

    def all(self) -> list[Document]:
        """
        Get all documents stored in the table.

        :returns: a list with all documents.
        """
        pass

    def search(self, cond: QueryLike) -> list[Document]:
        """
        Search for all documents matching a 'where' cond.

        :param cond: the condition to check against
        :returns: list of matching documents
        """

        # First, we check the query cache to see if it has results for this
        # query
        cached_results = self._query_cache.get(cond)
        if cached_results is not None:
            return cached_results[:]

        # Perform the search by applying the query to all documents.
        # Then, only if the document matches the query, convert it
        # to the document class and document ID class.
        docs = [
            self.document_class(doc, self.document_id_class(doc_id))
            for doc_id, doc in self._read_table().items()
            if cond(doc)
        ]

        # Only cache cacheable queries.
        #
        # This weird `getattr` dance is needed to make MyPy happy as
        # it doesn't know that a query might have a `is_cacheable` method
        # that is not declared in the `QueryLike` protocol due to it being
        # optional.
        # See: https://github.com/python/mypy/issues/1424
        #
        # Note also that by default we expect custom query objects to be
        # cacheable (which means they need to have a stable hash value).
        # This is to keep consistency with TinyDB's behavior before
        # `is_cacheable` was introduced which assumed that all queries
        # are cacheable.
        is_cacheable: Callable[[], bool] = getattr(cond, 'is_cacheable',
                                                   lambda: True)
        if is_cacheable():
            # Update the query cache
            self._query_cache[cond] = docs[:]

        return docs

    @overload
    def get(self) -> NoReturn: ...

    @overload
    def get(
        self, cond: QueryLike, doc_id: None = ..., doc_ids: None = ...
    ) -> Optional[Document]: ...

    @overload
    def get(
        self, *, cond: QueryLike, doc_id: None = ..., doc_ids: None = ...
    ) -> Optional[Document]: ...

    @overload
    def get(
        self, cond: Optional[QueryLike], doc_id: int, doc_ids: Optional[list] = ...
    ) -> Optional[Document]: ...

    @overload
    def get(
        self, *, cond: Optional[QueryLike] = ..., doc_id: int, doc_ids: Optional[list] = ...,
    ) -> Optional[Document]: ...

    @overload
    def get(
        self, cond: Optional[QueryLike], doc_id: None, doc_ids: list
    ) -> list[Document]: ...

    @overload
    def get(
        self, cond: Optional[QueryLike], *, doc_id: None = ..., doc_ids: list
    ) -> list[Document]: ...

    @overload
    def get(
        self, *, cond: Optional[QueryLike] = ..., doc_id: None = ..., doc_ids: list
    ) -> list[Document]: ...

    def get(
        self,
        cond: Optional[QueryLike] = None,
        doc_id: Optional[int] = None,
        doc_ids: Optional[list] = None
    ):
        """
        Get exactly one document specified by a query or a document ID.
        However, if multiple document IDs are given then returns all
        documents in a list.
        
        Returns ``None`` if the document doesn't exist.

        :param cond: the condition to check against
        :param doc_id: the document's ID
        :param doc_ids: the document's IDs(multiple)

        :returns: the document(s) or ``None``
        """
        table = self._read_table()

        if doc_id is not None:
            # Retrieve a document specified by its ID
            raw_doc = table.get(str(doc_id), None)

            if raw_doc is None:
                return None

            # Convert the raw data to the document class
            return self.document_class(raw_doc, doc_id)

        elif doc_ids is not None:
            # Filter the table by extracting out all those documents which
            # have doc id specified in the doc_id list.

            # Since document IDs will be unique, we make it a set to ensure
            # constant time lookup
            doc_ids_set = set(str(doc_id) for doc_id in doc_ids)

            # Now return the filtered documents in form of list
            return [
                self.document_class(doc, self.document_id_class(doc_id))
                for doc_id, doc in table.items()
                if doc_id in doc_ids_set
            ]

        elif cond is not None:
            # Find a document specified by a query
            # The trailing underscore in doc_id_ is needed so MyPy
            # doesn't think that `doc_id_` (which is a string) needs
            # to have the same type as `doc_id` which is this function's
            # parameter and is an optional `int`.
            for doc_id_, doc in self._read_table().items():
                if cond(doc):
                    return self.document_class(
                        doc,
                        self.document_id_class(doc_id_)
                    )

            return None

        raise RuntimeError('You have to pass either cond or doc_id or doc_ids')

    def contains(
        self,
        cond: Optional[QueryLike] = None,
        doc_id: Optional[int] = None
    ) -> bool:
        """
        Check whether the database contains a document matching a query or
        an ID.

        If ``doc_id`` is set, it checks if the db contains the specified ID.

        :param cond: the condition use
        :param doc_id: the document ID to look for
        """
        pass

    def update(
        self,
        fields: Union[Mapping, Callable[[Mapping], None]],
        cond: Optional[QueryLike] = None,
        doc_ids: Optional[Iterable[int]] = None,
    ) -> list[int]:
        """
        Update all matching documents to have a given set of fields.

        When ``doc_ids`` is given, IDs that don't refer to an existing
        document are silently skipped, and the returned list only contains
        the IDs that were actually updated.

        :param fields: the fields that the matching documents will have
                       or a method that will update the documents
        :param cond: which documents to update
        :param doc_ids: a list of document IDs
        :returns: a list containing the updated document's ID
        """
        pass

    def update_multiple(
        self,
        updates: Iterable[
            tuple[Union[Mapping, Callable[[Mapping], None]], QueryLike]
        ],
    ) -> list[int]:
        """
        Update all matching documents to have a given set of fields.

        :returns: a list containing the updated document's ID
        """
        pass

    def upsert(self, document: Mapping, cond: Optional[QueryLike] = None) -> list[int]:
        """
        Update documents, if they exist, insert them otherwise.

        Note: This will update *all* documents matching the query.
        For example, if the query matches 3 documents, all 3 will be updated
        with the new data. If no documents match, a new one is inserted.

        Document argument can be a tinydb.table.Document object if you want
        to specify a doc_id.

        :param document: the document to insert or the fields to update
        :param cond: which document to look for, optional if you've passed a
        Document with a doc_id
        :returns: a list containing the updated documents' IDs
        """
        pass

    def remove(
        self,
        cond: Optional[QueryLike] = None,
        doc_ids: Optional[Iterable[int]] = None,
    ) -> list[int]:
        """
        Remove all matching documents.

        When ``doc_ids`` is given, IDs that don't refer to an existing
        document are silently skipped, and the returned list only contains
        the IDs that were actually removed.

        :param cond: the condition to check against
        :param doc_ids: a list of document IDs
        :returns: a list containing the removed documents' ID
        """
        pass

    def truncate(self) -> None:
        """
        Truncate the table by removing all documents.
        """
        pass

    def count(self, cond: QueryLike) -> int:
        """
        Count the documents matching a query.

        :param cond: the condition use
        """
        pass

    def clear_cache(self) -> None:
        """
        Clear the query cache.
        """
        pass

    def __len__(self):
        """
        Count the total number of documents in this table.
        """

        return len(self._read_table())

    def __iter__(self) -> Iterator[Document]:
        """
        Iterate over all documents stored in the table.

        :returns: an iterator over all documents.
        """

        # Iterate all documents and their IDs
        for doc_id, doc in self._read_table().items():
            # Convert documents to the document class
            yield self.document_class(doc, self.document_id_class(doc_id))

    def _get_next_id(self):
        """
        Return the ID for a newly inserted document.
        """
        pass

    def _read_table(self) -> dict[str, Mapping]:
        """
        Read the table data from the underlying storage.

        Documents and doc_ids are NOT yet transformed, as
        we may not want to convert *all* documents when returning
        only one document for example.
        """

        # Retrieve the tables from the storage
        tables = self._storage.read()

        if tables is None:
            # The database is empty
            return {}

        # Retrieve the current table's data
        try:
            table = tables[self.name]
        except KeyError:
            # The table does not exist yet, so it is empty
            return {}

        return table

    def _update_table(self, updater: Callable[[dict[int, Mapping]], None]):
        """
        Perform a table update operation.

        The storage interface used by TinyDB only allows to read/write the
        complete database data, but not modifying only portions of it. Thus,
        to only update portions of the table data, we first perform a read
        operation, perform the update on the table data and then write
        the updated data back to the storage.

        As a further optimization, we don't convert the documents into the
        document class, as the table data will *not* be returned to the user.
        """
        pass
