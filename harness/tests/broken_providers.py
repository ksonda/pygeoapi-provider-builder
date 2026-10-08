"""Deliberately broken providers for the harness self-tests (PLAN.md §12).

``Good`` follows the 0.24 contract; each subclass breaks one rule from
docs/pygeoapi-notes.md §5, and the harness must say which.
"""

from __future__ import annotations

import copy
from typing import Any

from pygeoapi.provider.base import BaseProvider, ProviderItemNotFoundError
from pygeoapi.provider.base_edr import BaseEDRProvider

FEATURES = [
    {
        'type': 'Feature',
        'id': i,
        'geometry': {'type': 'Point', 'coordinates': [float(i), float(i)]},
        'properties': {'name': f'item number {i}', 'value': i},
    }
    for i in range(1, 9)
]


class Good(BaseProvider):  # type: ignore[misc]
    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        self._fields = {'name': {'type': 'string'}, 'value': {'type': 'integer'}}

    def get_fields(self) -> dict[str, Any]:
        return self._fields

    def query(
        self,
        offset: int = 0,
        limit: int = 10,
        resulttype: str = 'results',
        select_properties: Any = None,
        skip_geometry: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        features = copy.deepcopy(FEATURES)[offset : offset + limit]
        for f in features:
            if select_properties:
                f['properties'] = {
                    k: v for k, v in f['properties'].items() if k in select_properties
                }
            if skip_geometry:
                f['geometry'] = None
        if resulttype == 'hits':
            features = []
        return {
            'type': 'FeatureCollection',
            'features': features,
            'numberMatched': len(FEATURES),
            'numberReturned': len(features),
        }

    def get(self, identifier: Any, **kwargs: Any) -> dict[str, Any] | None:
        for f in FEATURES:
            if str(f['id']) == str(identifier):
                return copy.deepcopy(f)
        raise ProviderItemNotFoundError(user_msg='item not found')


class StringFields(Good):
    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        self._fields = {'name': 'string', 'value': 'integer'}


class EmptyFields(Good):
    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        self._fields = {}


class NoFeaturesOnHits(Good):
    def query(self, resulttype: str = 'results', **kwargs: Any) -> dict[str, Any]:
        result = super().query(resulttype=resulttype, **kwargs)
        if resulttype == 'hits':
            del result['features']
        return result


class GetReturnsNone(Good):
    def get(self, identifier: Any, **kwargs: Any) -> dict[str, Any] | None:
        try:
            return super().get(identifier)
        except ProviderItemNotFoundError:
            return None


class EdrNoUnits(BaseEDRProvider):  # type: ignore[misc]
    def __init__(self, provider_def: dict[str, Any]):
        super().__init__(provider_def)
        self._fields = {'water_level': {'type': 'number', 'title': 'Water level'}}

    def get_fields(self) -> dict[str, Any]:
        return self._fields

    def position(self, **kwargs: Any) -> dict[str, Any]:
        return {}
