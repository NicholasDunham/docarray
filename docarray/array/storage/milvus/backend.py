import copy
import uuid
from typing import Optional, TYPE_CHECKING, Union, Dict
from dataclasses import dataclass, field
from pymilvus import connections, Collection, FieldSchema, DataType, CollectionSchema

from docarray.array.storage.base.backend import BaseBackendMixin
from docarray.helper import dataclass_from_dict

if TYPE_CHECKING:
    from docarray.typing import (
        DocumentArraySourceType,
    )


def always_true_expr(primary_key: str) -> str:
    """
    Returns a Milvus expression that is always true, thus allowing for the retrieval of all entries in a Collection
    Assumes that the primary key is of type DataType.VARCHAR

    :param primary_key: the name of the primary key
    :return: a Milvus expression that is always true for that primary key
    """
    return f'({primary_key} in ["1"]) or ({primary_key} not in ["1"])'


@dataclass
class MilvusConfig:
    n_dim: int
    collection_name: str = None
    host: str = 'localhost'
    port: Optional[Union[str, int]] = 19530  # 19530 for gRPC, 9091 for HTTP
    distance: str = 'IP'  # metric_type in milvus
    index_type: str = 'HNSW'
    index_config: Dict = None  # passed to milvus at index creation time
    collection_config: Dict = field(
        default_factory=dict
    )  # passed to milvus at collection creation time
    serialize_config: Dict = field(default_factory=dict)


class BackendMixin(BaseBackendMixin):
    def _init_storage(
        self,
        _docs: Optional['DocumentArraySourceType'] = None,
        config: Optional[Union[MilvusConfig, Dict]] = None,
        **kwargs,
    ):
        config = copy.deepcopy(config)
        if not config:
            raise ValueError('Empty config is not allowed for Elastic storage')
        elif isinstance(config, dict):
            config = dataclass_from_dict(MilvusConfig, config)

        if config.collection_name is None:
            id = uuid.uuid4().hex
            config.collection_name = 'docarray__' + id
        self._config = config

        self._connection_alias = 'docarray_default_connection'
        connections.connect(
            alias=self._connection_alias, host=config.host, port=config.port
        )

        self._collection = self._create_or_reuse_collection()
        self._offset2id_collection = self._create_or_reuse_offset2id_collection()

        super()._init_storage(_docs, config, **kwargs)

    def _create_or_reuse_collection(self):
        # TODO(johannes) add logic to re-use collection if already exists
        document_id = FieldSchema(
            name='document_id', dtype=DataType.VARCHAR, max_length=1024, is_primary=True
        )  # TODO(johannes) this max_length is completely arbitrary
        embedding = FieldSchema(
            name='embedding', dtype=DataType.FLOAT_VECTOR, dim=self._config.n_dim
        )
        serialized = FieldSchema(
            name='serialized', dtype=DataType.VARCHAR, max_length=65_535
        )  # this is the maximus allowed length in milvus, could be optimized

        schema = CollectionSchema(
            fields=[document_id, embedding, serialized],
            description='DocumentArray collection',
        )
        return Collection(
            name=self._config.collection_name,
            schema=schema,
            using=self._connection_alias,
            **self._config.collection_config,
        )

    def _create_or_reuse_offset2id_collection(self):
        # TODO(johannes) add logic to re-use collection if already exists
        document_id = FieldSchema(
            name='document_id', dtype=DataType.VARCHAR, max_length=1024
        )  # TODO(johannes) this max_length is completely arbitrary
        offset = FieldSchema(
            name='offset', dtype=DataType.VARCHAR, max_length=1024, is_primary=True
        )  # TODO(johannes) this max_length is completely arbitrary
        # TODO(johannes)
        # This is really stupid and hacky, but milvus needs at least one vector field to create a Collection
        # We probably need a better way to store offset2id, but this should unblock the implementation in the meantime
        dummy_vector = FieldSchema(
            name='dummy_vector', dtype=DataType.FLOAT_VECTOR, dim=1
        )

        schema = CollectionSchema(
            fields=[offset, document_id, dummy_vector],
            description='offset2id for DocumentArray',
        )

        return Collection(
            name=self._config.collection_name + '_offset2id',
            schema=schema,
            using=self._connection_alias,
            # **self._config.collection_config,  # we probably don't want to apply the same config here
        )

    def _ensure_unique_config(
        self,
        config_root: dict,
        config_subindex: dict,
        config_joined: dict,
        subindex_name: str,
    ) -> dict:
        if 'collection_name' not in config_subindex:
            config_joined['collection_name'] = (
                config_joined['collection_name'] + '_subindex_' + subindex_name
            )
        return config_joined

    def _doc_to_milvus_payload(self, doc):
        return [
            [doc.id],
            [doc.embedding],
            [doc.to_base64(**self._config.serialize_config)],
        ]

    def _docs_to_milvus_payload(self, docs):
        return [
            docs[:, 'id'],
            list(docs[:, 'embedding']),
            [doc.to_base64(**self._config.serialize_config) for doc in docs],
        ]