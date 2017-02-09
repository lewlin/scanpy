"""
Annotated Data
"""
import sys
from collections import Mapping
from enum import Enum
import numpy as np
from numpy import ma
from numpy.lib.recfunctions import append_fields
from scipy import sparse as sp
from scipy.sparse.sputils import IndexMixin

class StorageType(Enum):
    Array = np.ndarray
    Masked = ma.MaskedArray
    Sparse = sp.spmatrix

    @classmethod
    def classes(cls):
        return tuple(c.value for c in cls.__members__.values())

SMP_NAMES = 'smp_names'
VAR_NAMES = 'var_names'

class BoundRecArr(np.recarray):
    """
    A np.recarray which can be constructed from a dict.
    Is bound to AnnData to allow adding fields
    """
    def __new__(cls, source, name_col, parent, nr_row=None):
        if source is None:  # empty array
            cols = [np.arange(nr_row)]
            dtype = [(name_col, 'int64')]
        elif isinstance(source, np.recarray):
            cols = [source[n] for n in source.dtype.names]
            dtype = source.dtype
        else:
            if not isinstance(source, Mapping):
                raise ValueError(
                    'meta needs to be a recarray or dictlike, not {}'
                    .format(type(source)))
            # meta is dict-like
            names = list(source.keys())
            cols = [np.asarray(col) for col in source.values()]
            if name_col not in source:
                names.append(name_col)
                cols.append(np.arange(len(cols[0])))
            dtype = list(zip(names, [str(c.dtype) for c in cols]))
        try:
            dtype = np.dtype(dtype)
        except TypeError:
            # TODO: fix compat with Python 2
            # print(dtype, file=sys.stderr)
            raise

        arr = np.recarray.__new__(cls, (len(cols[0]),), dtype)
        arr._parent = parent
        arr._name_col = name_col

        for i, name in enumerate(dtype.names):
            arr[name] = np.array(cols[i])

        return arr

    @property
    def columns(self):
        return [c for c in self.dtype.names if not c == self._name_col]

    def __setitem__(self, key, value):
        if self._parent and key not in self.dtype.names:
            attr = 'smp' if self._name_col == SMP_NAMES else 'var'
            value = np.asarray(value)
            if len(value) > len(self):
                raise ValueError('New column has too many entries ({} > {})'
                                 .format(len(value), len(self)))
            source = append_fields(self, [key], [value],
                                   usemask=False, asrecarray=True)
            new = BoundRecArr(source, self._name_col, self._parent)
            setattr(self._parent, attr, new)
        else:
            super(BoundRecArr, self).__setitem__(key, value)

def _check_dimensions(data, smp, var):
    nr_smp, nr_var = data.shape
    if len(smp) != nr_smp:
        raise ValueError('Sample metadata needs to have the same amount of '
                         'rows as data has ({}), but has {} rows'
                         .format(nr_smp, smp.shape[0]))
    if len(var) != nr_var:
        raise ValueError('Feature metadata needs to have the same amount of '
                         'rows as data has columns ({}), but has {} rows'
                         .format(nr_var, var.shape[0]))

class AnnData(IndexMixin):
    def __init__(self, ddata=None, X=None, smp=None, var=None, vis=None, **meta):
        """
        Annotated Data

        Stores a data matrix X of dimensions nr_samples x nr_variables,
        e.g. nr_cells x nr_genes, with the possibility to store an arbitrary
        number of annotations for both samples and variables.

        You can access additional metadata elements directly from the AnnData:

        >>> adata = AnnData(np.eye(3), k=1)
        >>> assert adata['k'] == 1

        Visualization metadata (vis) is a dict mapping smp_meta column
        names to colors. Possible values can be either a palette (a list of
        colors) or a dict/function mapping values from the corresponding
        metadata column to colors, e.g.:

        >>> from collections import OrderedDict
        >>> from matplotlib import cm
        >>> vis = {
        ...     'Col1': ['#ff3300', '#ffcc88'],
        ...     'Col2': {'V1': '#ff3300', 'V2': '#ffcc88'},  # no order!
        ...     'Col3': OrderedDict([('V1', '#ff3300'), ('V2', '#ffcc88')]),
        ...     'Col4': cm.magma,
        ... }

        Parameters
        ----------
        ddata : dict, containing
            X : np.ndarray, np.ma.MaskedArray, sp.spmatrix
                A nr_samples x nr_variables data matrix.
            row_names : list, np.ndarray, optional
                A nr_samples array storing names for samples.
            col_names : list, np.ndarray, optional
                A nr_variables array storing names for variables.
            row : dict, optional
                A dict with row annotation.
        X : np.ndarray, np.ma.MaskedArray, sp.spmatrix
            A nr_samples x nr_variables data matrix.
        smp : np.recarray, dict
            A nr_samples x ? record array containing sample names (`smp_names`)
            and other sample annotation in the columns. A passed dict is
            converted to a record array.
        var : np.recarray, dict
            The same as `smp`, but of shape nr_variables x ? for annotation of
            variables.
        vis : dict
            A dict containing visualization metadata.
        **meta : dict
            Unstructured metadata for the whole dataset.

        Attributes
        ----------
        X, smp, var from the Parameters.
        """
        if ddata is not None:
            if 'X' in ddata:
                X = ddata['X']
                del ddata['X']
            if 'row_names' in ddata:
                row_names = ddata['row_names']
                smp = np.rec.fromarrays([np.asarray(row_names)],
                                         names=[SMP_NAMES])
                del ddata['row_names']
            elif 'smp_names' in ddata:
                smp_names = ddata['smp_names']
                smp = np.rec.fromarrays([np.asarray(smp_names)],
                                         names=[SMP_NAMES])
                del ddata['smp_names']
            if 'col_names' in ddata:
                col_names = ddata['col_names']
                var = np.rec.fromarrays([np.asarray(col_names)],
                                         names=[VAR_NAMES])
                del ddata['col_names']
            elif 'var_names' in ddata:
                var_names = ddata['var_names']
                var = np.rec.fromarrays([np.asarray(var_names)],
                                         names=[VAR_NAMES])
                del ddata['var_names']

        # check data type of X
        for s_type in StorageType:
            if isinstance(X, s_type.value):
                self.storage_type = s_type
                break
        else:
            class_names = ', '.join(c.__name__ for c in StorageType.classes())
            raise ValueError(
                'X needs to be of one of the following types [{}] not {}'
                .format(class_names, type(X)))

        if len(X.shape) == 1:
            X.shape = (X.shape[0], 1)
        if X.dtype.names is None and len(X.shape) != 2:
            raise ValueError('X needs to be 2-dimensional, not '
                             '{}D'.format(len(X.shape)))

        nr_smp, nr_var = X.shape

        self.X = X

        self.smp = BoundRecArr(smp, SMP_NAMES, self, nr_smp)
        self.var = BoundRecArr(var, VAR_NAMES, self, nr_var)

        _check_dimensions(X, self.smp, self.var)

        self.vis = vis or {}
        self._meta = meta

        if ddata is not None:
            for key, value in ddata.items():
                if not key in ['smp', 'row', 'var', 'col']:
                    self._meta[key] = value
                elif key == 'row':
                    for k, v in ddata['row'].items():
                        self.smp[k] = v
                elif key == 'smp':
                    for k, v in ddata['smp'].items():
                        self.smp[k] = v
                elif key == 'col':
                    for k, v in ddata['col'].items():
                        self.var[k] = v
                elif key == 'var':
                    for k, v in ddata['var'].items():
                        self.var[k] = v
                    
    def smp_keys(self):
        return list(self.smp.dtype.names)[1:]

    def var_keys(self):
        return list(self.var.dtype.names)[1:]

    def to_dict(self):
        smp = {k: self.smp[k] for k in self.smp_keys() if not k=='smp_names'}
        var = {k: self.var[k] for k in self.var_keys() if not k=='var_names'}
        d = {'X': self.X, 'smp': smp, 'var': var, 
             'smp_names': self.smp_names, 'var_names': self.var_names}
        for k, v in self._meta.items():
            d[k] = v
        return d

    @property
    def smp_names(self):
        return self.smp[SMP_NAMES]

    @smp_names.setter
    def smp_names(self, keys):
        self.smp[SMP_NAMES] = keys

    @property
    def var_names(self):
        return self.var[VAR_NAMES]

    @var_names.setter
    def var_names(self, keys):
        self.var[VAR_NAMES] = keys

    def __setattr__(self, key, value):
        names_col = dict(smp=SMP_NAMES, var=VAR_NAMES).get(key)
        if names_col and not isinstance(value, BoundRecArr):  # if smp/var is set, give it the right class
            names_orig, dim = (self.smp_names, 0) if names_col == SMP_NAMES else (self.var_names, 1)
            value_orig, value = value, BoundRecArr(value, names_col, self)
            if len(value) != self.X.shape[dim]:
                raise ValueError('New value for {!r} was converted to a reacarray of length {} instead of {}'
                                 .format(key, len(value_orig), len(self)))
            if (value[names_col] == np.arange(self.X.shape[dim])).all():  # TODO: add to constructor
                value[names_col] = names_orig
        object.__setattr__(self, key, value)

    def _unpack_index(self, index):
        smp, var = super(AnnData, self)._unpack_index(index)
        if isinstance(smp, int):
            smp = slice(smp, smp+1)
        if isinstance(var, int):
            var = slice(var, var+1)
        return smp, var

    def __delitem__(self, index):
        smp, var = self._unpack_index(index)
        del self.X[smp, var]
        if var == slice(None):
            del self.smp.iloc[smp, :]
        if smp == slice(None):
            del self.var.iloc[var, :]

    def __getitem__(self, index):
        # return element from _meta if index is string
        if isinstance(index, str):
            return self._meta[index]
        # otherwise unpack index
        smp, var = self._unpack_index(index)
        X = self.X[smp, var]
        smp_meta = self.smp[smp]
        var_meta = self.var[var]
        assert smp_meta.shape[0] == X.shape[0], (smp, smp_meta)
        assert var_meta.shape[0] == X.shape[1], (var, var_meta)
        adata = AnnData(X=X, smp=smp_meta, var=var_meta, 
                       vis=self.vis, **self._meta)
        return adata

    def __setitem__(self, index, val):
        if isinstance(index, str):
            self._meta[index] = val
            return

        samp, feat = self._unpack_index(index)
        self.X[samp, feat] = val

    def __contains__(self, item):
        return item in self._meta

    def get(self, key, default=None):
        return self._meta.get(key, default)

    def __len__(self):
        return self.X.shape[0]

    def transpose(self):
        smp = np.rec.array(self.var)
        smp.dtype.names = [SMP_NAMES if n == VAR_NAMES else n
                           for n in smp.dtype.names]
        var = np.rec.array(self.smp)
        var.dtype.names = [VAR_NAMES if n == SMP_NAMES else n
                           for n in var.dtype.names]
        return AnnData(X=self.X.T, smp=smp, var=var, vis=self.vis, **self._meta)

    T = property(transpose)

def test_creation():
    AnnData(np.array([[1, 2], [3, 4]]))
    AnnData(ma.array([[1, 2], [3, 4]], mask=[0, 1, 1, 0]))
    AnnData(sp.eye(2))
    AnnData(
        np.array([[1, 2, 3], [4, 5, 6]]),
        dict(Smp=['A', 'B']),
        dict(Feat=['a', 'b', 'c']))

    assert AnnData(np.array([1, 2])).X.shape == (2, 1)

    from pytest import raises
    raises(ValueError, AnnData,
           np.array([[1, 2], [3, 4]]),
           dict(TooLong=[1, 2, 3, 4]))

def test_ddata():
    ddata = dict(
        X=np.array([[1, 2, 3], [4, 5, 6]]),
        row_names=['A', 'B'],
        col_names=['a', 'b', 'c'])
    AnnData.from_ddata(**ddata)

def test_names():
    adata = AnnData(
        np.array([[1, 2, 3], [4, 5, 6]]),
        dict(smp_names=['A', 'B']),
        dict(var_names=['a', 'b', 'c']))

    assert adata.smp_names.tolist() == 'A B'.split()
    assert adata.var_names.tolist() == 'a b c'.split()

def test_get_subset():
    mat = AnnData(np.array([[1, 2, 3], [4, 5, 6]]))

    assert mat[0, 0].X.tolist() == [[1]]
    assert mat[0, :].X.tolist() == [[1, 2, 3]]
    assert mat[:, 0].X.tolist() == [[1], [4]]
    assert mat[:, [0, 1]].X.tolist() == [[1, 2], [4, 5]]
    assert mat[:, 1:3].X.tolist() == [[2, 3], [5, 6]]

def test_get_subset_meta():
    mat = AnnData(np.array([[1, 2, 3], [4, 5, 6]]),
                  dict(Smp=['A', 'B']),
                  dict(Feat=['a', 'b', 'c']))

    assert mat[0, 0].smp['Smp'].tolist() == ['A']
    assert mat[0, 0].var['Feat'].tolist() == ['a']

def test_append_meta_col():
    mat = AnnData(np.array([[1, 2, 3], [4, 5, 6]]))

    mat.smp['new_col'] = [1, 2]

    from pytest import raises
    with raises(ValueError):
        mat.smp['new_col2'] = 'far too long'.split()

def test_set_meta():
    mat = AnnData(np.array([[1, 2, 3], [4, 5, 6]]))

    mat.smp = dict(smp_names=[1, 2])
    assert isinstance(mat.smp, BoundRecArr)
    assert len(mat.smp.dtype) == 1

    mat.smp = dict(a=[1, 2])  # leave smp_names and a custom column
    assert isinstance(mat.smp, BoundRecArr)
    assert len(mat.smp.dtype) == 2
    assert mat.smp_names.tolist() == [1, 2]

    from pytest import raises
    with raises(ValueError):
        mat.smp = dict(a=[1, 2, 3])