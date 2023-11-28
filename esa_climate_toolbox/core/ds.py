# The MIT License (MIT)
# Copyright (c) 2023 ESA Climate Change Initiative
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import datetime
import geopandas as gpd
from typing import Any
from typing import Container
from typing import Dict
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import Tuple
from typing import Union
import xarray as xr
import yaml

from xcube.core.mldataset import MultiLevelDataset
from xcube.core.select import select_subset
import xcube.core.store as xcube_store
from xcube.util.assertions import assert_instance
from xcube.util.progress import add_progress_observers
from xcube.util.progress import ProgressObserver
from xcube.util.progress import ProgressState

from esa_climate_toolbox.conf.defaults import STORES_CONF_FILE
from ..constants import ECT_STORE_ID
from ..constants import ECT_ZARR_STORE_ID
from .types import PolygonLike
from .types import TimeRangeLike
from .types import ValidationError
from .types import VarNamesLike
from ..util.monitor import ChildMonitor
from ..util.monitor import Monitor

_DEFAULT_FILE_STORE_ID_PREFIX = 'local'
_OUTPUT_STORE = None

ECT_DATA_STORE_POOL = xcube_store.DataStorePool()


class DataAccessError(Exception):
    """
    Exceptions produced by the data stores of the ESA Climate Toolbox,
    used to report any problems handling data.
    """


class XcubeProgressObserver(ProgressObserver):

    def __init__(self, monitor: Monitor):
        self._monitor = monitor
        self._latest_completed_work = 0.0

    def on_begin(self, state_stack: Sequence[ProgressState]):
        if len(state_stack) == 1:
            self._monitor.start(state_stack[0].label, state_stack[0].total_work)

    def on_update(self, state_stack: Sequence[ProgressState]):
        if state_stack[0].completed_work > self._latest_completed_work:
            self._monitor.progress(state_stack[0].completed_work
                                   - self._latest_completed_work,
                                   state_stack[-1].label)
            self._latest_completed_work = state_stack[0].completed_work

    def on_end(self, state_stack: Sequence[ProgressState]):
        if len(state_stack) == 1:
            self._monitor.done()


def list_stores() -> List[str]:
    return ECT_DATA_STORE_POOL.store_instance_ids


def list_ecvs() -> List[str]:
    store = ECT_DATA_STORE_POOL.get_store(ECT_STORE_ID)
    data_ids = store.list_data_ids()
    return list(set([data_id.split('.')[1] for data_id in data_ids]))


def list_ecv_datasets(
        ecv: str,
        data_type: xcube_store.DataTypeLike = None,
        include_attrs: Container[str] = None
) -> Union[List[str], List[Tuple[str, Dict[str, Any]]]]:
    ecvs = list_ecvs()
    if ecv.upper() not in ecvs:
        raise ValueError(f'"{ecv}" is not an Essential Climate Variable '
                         f'provided by the ESA Climate Toolbox. Please choose '
                         f'one of the following: {", ".join(ecvs)}')
    ecv = ecv.upper()
    ecv_datasets = []
    for store_instance_id in ECT_DATA_STORE_POOL.store_instance_ids:
        store = ECT_DATA_STORE_POOL.get_store(store_instance_id)
        data_ids = store.list_data_ids(data_type, include_attrs)
        for data_id in data_ids:
            if ecv in data_id.upper():
                ecv_datasets.append((data_id, store_instance_id))
    return ecv_datasets


def _get_store_id_from_prefix(prefix: str) -> str:
    count = 1
    store_id = f'{prefix}_{count}'
    while ECT_DATA_STORE_POOL.has_store_instance(store_id):
        count += 1
        store_id = f'{prefix}_{count}'
    return store_id


def _save_store_pool():
    with open(STORES_CONF_FILE, 'w') as fp:
        yaml.safe_dump(ECT_DATA_STORE_POOL.to_dict(), fp)


def add_local_store(root: str, store_id: str = None, max_depth: int = 1,
                    read_only: bool = False, includes: str = None,
                    excludes: str = None, title: str = None,
                    description: str = None, persist: bool = True) -> str:
    if store_id is not None and \
            ECT_DATA_STORE_POOL.has_store_instance(store_id):
        raise ValueError(
            f'There is already a store with the id "{store_id}" registered.'
            f' Please choose another id.'
        )
    if store_id is None:
        store_id = _get_store_id_from_prefix(_DEFAULT_FILE_STORE_ID_PREFIX)
    store_params = {
        'root': root,
        'max_depth': max_depth,
        'read_only': read_only,
        'includes': includes,
        'excludes': excludes
    }
    store_config = xcube_store.DataStoreConfig(
        store_id='file', store_params=store_params,
        title=title, description=description
    )
    ECT_DATA_STORE_POOL.add_store_config(store_id, store_config)
    if persist:
        _save_store_pool()
    return store_id


def add_store(
        store_type: str, store_params: Mapping[str, Any] = None,
        store_id: str = None, title: str = None, description: str = None,
        user_data: Any = None, persist: bool = True
) -> str:
    if store_id is not None and \
            ECT_DATA_STORE_POOL.has_store_instance(store_id):
        raise ValueError(
            f'There is already a store with the id "{store_id}" registered.'
            f' Please choose another id.'
        )
    if store_id is None:
        store_id = _get_store_id_from_prefix(store_type)
    store_config = xcube_store.DataStoreConfig(
        store_id=store_type, store_params=store_params, title=title,
        description=description, user_data=user_data
    )
    ECT_DATA_STORE_POOL.add_store_config(store_id, store_config)
    if persist:
        _save_store_pool()
    return store_id


def get_store(store_id: str):
    return ECT_DATA_STORE_POOL.get_store(store_id)


def list_datasets(
        store_id: str = None,
        data_type: xcube_store.DataTypeLike = None,
        include_attrs: Container[str] = None
) -> Union[List[str], List[Tuple[str, Dict[str, Any]]]]:
    if store_id:
        return ECT_DATA_STORE_POOL.get_store(store_id).list_data_ids(
            data_type, include_attrs
        )
    datasets = []
    for store_instance_id in ECT_DATA_STORE_POOL.store_instance_ids:
        store = ECT_DATA_STORE_POOL.get_store(store_instance_id)
        datasets.extend(store.list_data_ids(data_type, include_attrs))
    return datasets


def remove_store(store_id: str, persist: bool = True):
    if store_id in [ECT_STORE_ID, ECT_ZARR_STORE_ID]:
        raise ValueError(f'Cannot remove essential store "{store_id}".')
    if store_id not in ECT_DATA_STORE_POOL.store_instance_ids:
        raise ValueError(f'No store named "{store_id}" found.')
    ECT_DATA_STORE_POOL.remove_store_config(store_id)
    if persist:
        _save_store_pool()


def get_output_store_id() -> Optional[str]:
    return _OUTPUT_STORE


def set_output_store(store_id: str):
    if store_id not in ECT_DATA_STORE_POOL.store_instance_ids:
        raise ValueError(f'Could not set "{store_id}" as default output store. '
                         f'Store is not registered.')
    global _OUTPUT_STORE
    _OUTPUT_STORE = store_id


def find_data_store(
        ds_id: str
) -> Tuple[Optional[str], Optional[xcube_store.DataStore]]:
    """
    Find the data store that includes the given *ds_id*.
    This will raise an exception if the *ds_id* is given
    in more than one data store.

    :param ds_id:  A data source identifier.
    :return: All data sources matching the given constrains.
    """
    results = []
    for store_instance_id in ECT_DATA_STORE_POOL.store_instance_ids:
        data_store = ECT_DATA_STORE_POOL.get_store(store_instance_id)
        if data_store.has_data(ds_id):
            results.append((store_instance_id, data_store))
    if len(results) > 1:
        raise ValidationError(
            f'{len(results)} data sources found for the given ID {ds_id!r}'
        )
    if len(results) == 1:
        return results[0]
    return None, None


def open_data(dataset_id: str,
              time_range: TimeRangeLike.TYPE = None,
              region: PolygonLike.TYPE = None,
              var_names: VarNamesLike.TYPE = None,
              data_store_id: str = None,
              monitor: Monitor = Monitor.NONE) -> Tuple[Any, str]:
    """
    Open a dataset from a data store.

    :param dataset_id: The identifier of the dataset. Must not be empty.
    :param time_range: An optional time constraint comprising start and end
        date. If given, it must be a :py:class:`TimeRangeLike`.
    :param region: An optional region constraint.
        If given, it must be a :py:class:`PolygonLike`.
    :param var_names: Optional names of variables to be included.
        If given, it must be a :py:class:`VarNamesLike`.
    :param data_store_id: Optional data store identifier. If given, *ds_id*
        will only be looked up from the specified data store.
    :param monitor: A progress monitor
    :return: A tuple consisting of a new dataset instance and its id
    """
    if not dataset_id:
        raise ValidationError('No data source given')

    if data_store_id:
        data_store = ECT_DATA_STORE_POOL.get_store(data_store_id)
    else:
        data_store_id, data_store = find_data_store(ds_id=dataset_id)
        if not data_store:
            raise ValidationError(
                f"No data store found that contains the ID '{dataset_id}'"
            )

    data_type = None
    potential_data_types = data_store.get_data_types_for_data(dataset_id)
    for potential_data_type in potential_data_types:
        if xcube_store.DATASET_TYPE.is_super_type_of(potential_data_type):
            data_type = potential_data_type
            break
    if data_type is None:
        raise ValidationError(f"Could not open '{dataset_id}' as dataset.")
    openers = data_store.get_data_opener_ids(dataset_id, data_type)
    if len(openers) == 0:
        raise DataAccessError(f'Could not find an opener for "{dataset_id}".')
    opener_id = openers[0]

    open_work = 10
    subset_work = 0

    open_schema = data_store.get_open_data_params_schema(dataset_id, opener_id)
    open_args = {}

    subset_args = {}
    if var_names:
        var_names_list = VarNamesLike.convert(var_names)
        if 'variable_names' in open_schema.properties:
            open_args['variable_names'] = var_names_list
        elif 'drop_variables' in open_schema.properties:
            data_desc = data_store.describe_data(dataset_id, data_type)
            if hasattr(data_desc, 'data_vars') \
                    and isinstance(getattr(data_desc, 'data_vars'), dict):
                open_args['drop_variables'] = [
                    var_name for var_name in data_desc.data_vars.keys()
                    if var_name not in var_names_list
                ]
        else:
            subset_args['var_names'] = var_names_list
            subset_work += 1

    if time_range:
        time_range = TimeRangeLike.convert(time_range)
        time_range = [datetime.datetime.strftime(time_range[0], '%Y-%m-%d'),
                      datetime.datetime.strftime(time_range[1], '%Y-%m-%d')]
        if 'time_range' in open_schema.properties:
            open_args['time_range'] = time_range
        else:
            subset_args['time_range'] = time_range
            subset_work += 1

    if region:
        bbox = list(PolygonLike.convert(region).bounds)
        if 'bbox' in open_schema.properties:
            open_args['bbox'] = bbox
        else:
            subset_args['bbox'] = bbox
            subset_work += 1

    with monitor.starting('Open dataset', open_work + subset_work):
        with add_progress_observers(
                XcubeProgressObserver(ChildMonitor(monitor, open_work))
        ):
            dataset = data_store.open_data(
                data_id=dataset_id, opener_id=opener_id, **open_args
            )

        dataset = select_subset(dataset, **subset_args)
        monitor.progress(subset_work)

    return dataset, dataset_id


def get_supported_formats(data: Any, store_id: str) -> List[str]:
    if store_id is not None and \
            store_id not in ECT_DATA_STORE_POOL.store_instance_ids:
        raise ValueError(f'Unknown Data Store "{store_id}".')
    store = ECT_DATA_STORE_POOL.get_store(store_instance_id=store_id)
    assert isinstance(store, xcube_store.MutableDataStore)
    data_type = _get_data_type(data)
    writer_ids = store.get_data_writer_ids(data_type)
    return [writer_id.split(':')[1] for writer_id in writer_ids]


def write_data(
        data: Any, data_id: str = None, store_id: str = None,
        format_id: str = None, replace: bool = False,
        monitor: Monitor = Monitor.NONE
) -> str:
    if store_id is not None and \
            store_id not in ECT_DATA_STORE_POOL.store_instance_ids:
        raise ValueError(f'Unknown Data Store "{store_id}".')
    if store_id is None and _OUTPUT_STORE is None:
        raise ValueError('No default output store set. '
                         'Must specify target store id.')
    store = ECT_DATA_STORE_POOL.get_store(store_instance_id=store_id)
    assert_instance(store, xcube_store.MutableDataStore)
    data_type = _get_data_type(data)
    writer_id = None
    if format_id:
        writer_ids = store.get_data_writer_ids(data_type)
        supported_format_ids = \
            [writer_id.split(':')[1] for writer_id in writer_ids]
        try:
            format_index = supported_format_ids.index(format_id)
        except ValueError:
            raise ValidationError(
                f'Format "{format_id}" is not supported by data store '
                f'"{store_id}". Must be one of the following: '
                f'{", ".join(supported_format_ids)}'
            )
        writer_id = writer_ids[format_index]

    write_work = 10

    with monitor.starting('Writing dataset', write_work):
        with add_progress_observers(
                XcubeProgressObserver(ChildMonitor(monitor, write_work))
        ):
            dataset_id = store.write_data(
                data=data, data_id=data_id, writer_id=writer_id, replace=replace
            )

    return dataset_id


def _get_data_type(data: Any) -> str:
    if isinstance(data, xr.Dataset):
        return xcube_store.DATASET_TYPE.alias
    elif isinstance(data, MultiLevelDataset):
        return xcube_store.MULTI_LEVEL_DATASET_TYPE.alias
    elif isinstance(data, gpd.GeoDataFrame):
        return xcube_store.GEO_DATA_FRAME_TYPE.alias
    else:
        raise ValidationError('Cannot determine data type.')


def get_search_params(store_id: str, data_type: str) -> Dict:
    store = ECT_DATA_STORE_POOL.get_store(store_id)
    search_params = store.get_search_params_schema(data_type)
    return search_params.to_dict()


def search(store_id: str, data_type: str = None, **search_params) -> List[Dict]:
    store = ECT_DATA_STORE_POOL.get_store(store_id)
    data_descriptors = list(store.search_data(
        data_type=data_type, **search_params
    ))
    return [data_descriptor.to_dict() for data_descriptor in data_descriptors]
