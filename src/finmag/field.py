"""
Representation of scalar and vector fields, as well as
operations on them backed by a dolfin function.

This module exists because things like per-node operations or exporting of
field values to convenient formats are awkward to do in dolfin currently.
Additionally, some things are non-trivial to get right, especially in parallel.
This class therefore acts as a "single point of contant", so that we don't
duplicate code all over the FinMag code base.

"""
import logging
import dolfin as df
import numpy as np
import numbers
import os
import dolfinh5tools
from finmag.util import helpers
from finmag.util.helpers import expression_from_python_function
from finmag.util.visualization import plot_dolfin_function

log = logging.getLogger(name="finmag")


def associated_scalar_space(functionspace):
    """
    Given any dolfin function space (which may be a scalar or vector space),
    return a scalar function space on the same mesh defined by the same finite
    element family and degree.

    """
    fs_family = functionspace.ufl_element().family()
    fs_degree = functionspace.ufl_element().degree()
    return df.FunctionSpace(functionspace.mesh(), fs_family, fs_degree)


class Field(object):
    """
    Representation of scalar and vector fields using a dolfin function.

    You can set the field values using a wide range of object types:
        - tuples, lists, ints, floats, basestrings, numpy arrrays
        - dolfin constants, expressions and functions
        - callables
        - files in hdf5

    The Field class provides raw access to the field at some particular point
    or all nodes. It also computes derived entities of the field, such as
    spatially averaged energy. It outputs data suited for visualisation
    or storage.

    """
    def __init__(self, functionspace, value=None, normalised=False, name=None, unit=None):
        self.functionspace = functionspace
        self.f = df.Function(self.functionspace)
        self.name = name

        if value is not None:
            self.value = value
            self.set(value, normalised=normalised)

        if name is not None:
            self.f.rename(name, name)  # set function's name and label

        self.unit = unit

        functionspace_family = self.f.ufl_element().family()
        if functionspace_family == 'Lagrange':
            dim = self.value_dim()
            self.v2d_xyz, self.v2d_xxx, self.d2v_xyz, self.d2v_xxx = helpers.build_maps(self.functionspace, dim)

    def __call__(self, x):
        """
        Shorthand so user can do field(x) instead of field.f(x) to interpolate.

        """
        return self.f(x)

    def assert_is_scalar_field(self):
        if self.value_dim() != 1:
            raise ValueError(
                "This operation is only defined for scalar fields.")

    def from_array(self, arr):
        assert isinstance(arr, np.ndarray)
        if arr.shape == (3,) and (isinstance(self.functionspace, df.FunctionSpace) and
                                  self.functionspace.num_sub_spaces() == self.value_dim()):
            self.from_constant(df.Constant(arr))
        else:
            if arr.shape[0] == self.f.vector().local_size():
                self.f.vector().set_local(arr)
            else:
                # in serial, local_size == size, so this will only warn in parallel
                log.warning("Global setting of field values by overwriting with np.array.")
                self.f.vector()[:] = arr

    def from_callable(self, func):
        assert hasattr(func, "__call__") and not isinstance(func, df.Function)
        expr = expression_from_python_function(func, self.functionspace)
        self.from_expression(expr)

    def from_constant(self, constant):
        assert isinstance(constant, df.Constant)
        self.f.assign(constant)

    def from_expression(self, expr, **kwargs):
        """
        Set field values using dolfin expression or the ingredients for one,
        in which case it will build the dolfin expression for you.

        """
        if not isinstance(expr, df.Expression):
            if isinstance(self.functionspace, df.FunctionSpace) and self.functionspace.num_sub_spaces() == 0:
                assert (isinstance(expr, basestring) or
                        isinstance(expr, (tuple, list)) and len(expr) == 1)
                expr = str(expr)  # dolfin does not like unicode in the expression
            if isinstance(self.functionspace, df.FunctionSpace) and \
               self.functionspace.num_sub_spaces() == 3:
                assert isinstance(expr, (tuple, list)) and len(expr) == 3
                assert all(isinstance(item, basestring) for item in expr)
                map(str, expr)  # dolfin does not like unicode in the expression
            expr = df.Expression(expr, degree=1, **kwargs)
        temp_function = df.interpolate(expr, self.functionspace)
        self.f.vector().set_local(temp_function.vector().get_local())

    def from_field(self, field):
        assert isinstance(field, Field)
        if self.functionspace == field.functionspace:
            self.f.vector().set_local(field.f.vector().get_local())
        else:
            temp_function = df.interpolate(field.f, self.functionspace)
            self.f.vector().set_local(temp_function.vector().get_local())

    def from_function(self, function):
        assert isinstance(function, df.Function)
        self.f.vector().set_local(function.vector().get_local())

    def from_generic_vector(self, vector):
        assert isinstance(vector, df.GenericVector)
        self.f.vector().set_local(vector.get_local())

    def from_sequence(self, seq):
        assert isinstance(seq, (tuple, list))
        self._check_can_set_vector_value(seq)
        self.from_constant(df.Constant(seq))

    def _check_can_set_scalar_value(self):
        if not self.functionspace.num_sub_spaces() == 0:
            raise ValueError("Cannot set vector field with scalar value.")

    def _check_can_set_vector_value(self, seq):
        if not (isinstance(self.functionspace, df.FunctionSpace) and self.functionspace.num_sub_spaces() == self.value_dim()):
            raise ValueError("Cannot set scalar field with vector value.")
        if len(seq) != self.functionspace.num_sub_spaces():
            raise ValueError(
                "Cannot set vector field with value of non-matching dimension "
                "({} != {})", len(seq), self.functionspace.num_sub_spaces())

    def set(self, value, normalised=False, **kwargs):
        """
        Set field values using `value` and normalise if `normalised` is True.

        The parameter `value` can be one of many different types,
        as described in the class docstring. This method avoids the user
        having to find the correct `from_*` method to call.

        """
        if isinstance(value, df.Constant):
            self.from_constant(value)
        elif isinstance(value, df.Expression):
            self.from_expression(value)
        elif isinstance(value, df.Function):
            self.from_function(value)
        elif isinstance(value, Field):
            self.from_field(value)
        elif isinstance(value, df.GenericVector):
            self.from_generic_vector(value)
        elif isinstance(value, (int, float)):
            self._check_can_set_scalar_value()
            self.from_constant(df.Constant(value))
        elif isinstance(value, basestring):
            self._check_can_set_scalar_value()
            self.from_expression(value, **kwargs)
        elif (isinstance(value, (tuple, list)) and
              all(isinstance(item, basestring) for item in value)):
            self._check_can_set_vector_value(value)
            self.from_expression(value, **kwargs)
        elif isinstance(value, (tuple, list)):
            self.from_sequence(value)
        elif isinstance(value, np.ndarray):
            self.from_array(value)
        elif hasattr(value, '__call__'):
            # this matches df.Function as well, so this clause needs to
            # be after the one checking for df.Function
            self.from_callable(value)
        else:
            raise TypeError("Can't set field values using {}.".format(type(value)))

        if normalised:
            self.normalise()

    def set_with_numpy_array_debug(self, value, normalised=False):
        """ONLY for debugging"""
        self.f.vector().set_local(value)

        if normalised:
            self.normalise()

    def get_ordered_numpy_array(self):
        """
        For a scalar field, return the dolfin function as an ordered
        numpy array, such that the field values are in the same order
        as the vertices of the underlying mesh (as returned by
        `mesh.coordinates()`).

        Note:

        This function is only defined for scalar fields and raises an
        error if it is applied to a vector field. For the latter, use
        either

            get_ordered_numpy_array_xxx

        or

            get_ordered_numpy_array_xyz

        depending on the order in which you want the values to be returned.

        """
        self.assert_is_scalar_field()
        return self.get_ordered_numpy_array_xxx()

    def get_ordered_numpy_array_xyz(self):
        """
        Returns the dolfin function as an ordered numpy array, so that
        all components at the same node are grouped together. For example,
        for a 3d vector field the values are returned in the following order:

          [f_1x, f_1y, f_1z,  f_2x, f_2y, f_2z,  f_3x, f_3y, f_3z,  ...]

        Note: In the case of a scalar field this function is equivalent to
        `get_ordered_numpy_array_xxx` (but for vector fields they yield
        different results).
        """
        return self.get_numpy_array_debug()[self.v2d_xyz]

    def get_ordered_numpy_array_xxx(self):
        """
        Returns the dolfin function as an ordered numpy array, so that
        all x-components at different nodes are grouped together, and
        similarly for the other components. For example, for a 3d
        vector field the values are returned in the following order:

          [f_1x, f_2x, f_3x, ...,  f_1y, f_2y, f_3y, ...,  f_1z, f_2z, f_3z, ...]

        Note: In the case of a scalar field this function is equivalent to
        `get_ordered_numpy_array_xyz` (but for vector fields they yield
        different results).
        """
        return self.get_numpy_array_debug()[self.v2d_xxx]

    # def order2_to_order1(self, order2):
    #     """Returns the dolfin function as an ordered numpy array, so that
    #     in the case of vector fields all components of different nodes
    #     are grouped together."""
    #     n = len(order2)
    #     return ((order2.reshape(3, n/3)).transpose()).reshape(n)
    #
    # def order1_to_order2(self, order1):
    #     """Returns the dolfin function as an ordered numpy array, so that
    #     in the case of vector fields all components of different nodes
    #     are grouped together."""
    #     n = len(order1)
    #     return ((order1.reshape(n/3, 3)).transpose()).reshape(n)

    def set_with_ordered_numpy_array(self, ordered_array):
        """
        Set the scalar field using an ordered numpy array (where the field
        values have the same ordering as the vertices in the underlying
        mesh).

        This function raises an error if the field is not a scalar field.
        """
        self.assert_is_scalar_field()
        self.set_with_ordered_numpy_array_xxx(ordered_array)

    def set_with_ordered_numpy_array_xyz(self, ordered_array):
        """
        Set the field using an ordered numpy array in "xyz" order.
        For example, for a 3d vector field the values should be
        arranged as follows:

          [f_1x, f_1y, f_1z, f_2x, f_2y, f_2z, f_3x, f_3y, f_3z, ...]

        For a scalar field this function is equivalent to
        `set_with_ordered_numpy_array_xxx`.
        """
        self.set(ordered_array[self.d2v_xyz])

    def set_with_ordered_numpy_array_xxx(self, ordered_array):
        """
        Set the field using an ordered numpy array in "xxx" order.
        For example, for a 3d vector field the values should be
        arranged as follows:

          [f_1x, f_2x, f_3x, ..., f_1y, f_2y, f_3y, ..., f_1z, f_2z, f_3z, ...]

        For a scalar field this function is equivalent to
        `set_with_ordered_numpy_array_xyz`.
        """
        self.set(ordered_array[self.d2v_xxx])

    def set_random_values(self, vrange=[-1, 1]):
        """
        This is a helper function useful for debugging. It fills the array
        with random values where each coordinate is uniformly distributed
        from the half-open interval `vrange` (default: vrange=[-1, 1)).

        """
        shape = self.f.vector().array().shape
        a, b = vrange
        vals = np.random.random_sample(shape) * float(b - a) + a
        self.set(vals)

    def as_array(self):
        return self.f.vector().array()

    def as_vector(self):
        return self.f.vector()

    def get_numpy_array_debug(self):
        """ONLY for debugging"""
        return self.f.vector().array()

    def is_scalar_field(self):
        """
        Return `True` if the Field is a scalar field and `False` otherwise.
        """
        if self.functionspace.num_sub_spaces() == 0:
            return True

    def is_constant(self, eps=1e-14):
        """
        Return `True` if the Field has a unique constant value across the mesh
        and `False` otherwise.

        """
        # Scalar field
        if self.is_scalar_field():
            maxval = self.f.vector().max()  # global (!) maximum value
            minval = self.f.vector().min()  # global (!) minimum value
            return (maxval - minval) < eps
        # Vector field
        else:
            raise NotImplementedError()

    def as_constant(self, eps=1e-14):
        """
        If the Field has a unique constant value across the mesh, return this value.
        Otherwise a RuntimeError is raised.
        """
        if self.is_scalar_field():
            maxval = self.f.vector().max()  # global (!) maximum value
            minval = self.f.vector().min()  # global (!) minimum value
            if (maxval - minval) < eps:
                return maxval
            else:
                raise RuntimeError("Field does not have a unique constant value.")
        else:
            raise NotImplementedError()

    def average(self, dx=df.dx):
        """
        Return the spatial field average.

        Returns:
          f_average (float for scalar and np.ndarray for vector field)

        """
        # Compute the mesh "volume". For 1D mesh "volume" is the length and
        # for 2D mesh is the area of the mesh.
        volume = df.assemble(df.Constant(1) * dx(self.mesh()))

        # Scalar field.
        if self.is_scalar_field():
            return df.assemble(self.f * dx) / volume

        # Vector field.
        else:
            f_average = []
            # Compute the average for every vector component independently.
            for i in xrange(self.value_dim()):
                f_average.append(df.assemble(self.f[i] * dx))

            return np.array(f_average) / volume

    def coords_and_values(self, t=None):
        """
        If the field is defined on a function space with degrees of freedom
        at mesh vertices only, return a list of mesh coordinates and associated
        field values (in the same order).

        """
        # The function values are defined at mesh nodes only for
        # specific function space families. In finmag, the only families
        # of interest are Lagrange (CG) and Discontinuous Lagrange (DG).
        # Therefore, if the function space is not CG-family-type,
        # values cannot be associated to mesh nodes.
        functionspace_family = self.f.ufl_element().family()
        if functionspace_family == 'Discontinuous Lagrange':
            # Function values are not defined at nodes.
            raise TypeError('The function space is Discontinuous Lagrange '
                            '(DG) family type, for which the function values '
                            'are not defined at mesh nodes.')

        elif functionspace_family == 'Lagrange':
            # Function values are defined at nodes.
            coords = self.functionspace.mesh().coordinates()
            num_nodes = self.functionspace.mesh().num_vertices()
            f_array = self.f.vector().array()  # numpy array
            vtd_map = df.vertex_to_dof_map(self.functionspace)

            value_dim = self.value_dim()
            values = np.empty((num_nodes, value_dim))
            for i in xrange(num_nodes):
                try:
                    values[i, :] = f_array[vtd_map[value_dim * i:
                                                   value_dim * (i + 1)]]
                except IndexError:
                    # This only occurs in parallel and is probably related
                    # to ghost nodes. I thought we could ignore those, but
                    # this doesn't seem to be true since the resulting
                    # array of function values has the wrong size. Need to
                    # investigate.  (Max, 15/05/2014)
                    raise NotImplementedError("TODO")

            if value_dim == 1:
                values.shape = (num_nodes,)  # convert to scalar field
            return coords, values

        else:
            raise NotImplementedError('This method is not implemented '
                                      'for {} family type function '
                                      'spaces.'.format(functionspace_family))

    def __add__(self, other):
        result = Field(self.functionspace)
        result.set(self.f.vector() + other.f.vector())
        return result

    def coerce_scalar_field(self, value):
        """
        Coerce `value` into a scalar field defined over the same mesh
        (and using the same finite element family) as the current field.

        """
        if not isinstance(value, Field):
            S1 = associated_scalar_space(self.functionspace)
            try:
                # Try to coerce 'value' into a scalar function space
                # on the same mesh.
                res = Field(S1, value)
            except:
                print("Error: cannot coerce into scalar field: {}".format(value))
                raise
        else:
            value.assert_is_scalar_field()
            res = value
        return res

    def __mul__(self, other):
        # We use Claas Abert's 'point measure hack' to multiply the dolfin
        # function self.f with the scalar function a.f at each vertex.
        # Note that if 'other' is just a number, it should be possible to
        # say: result.set(self.f.vector() * other), but this currently throws
        # a PETSc error.  -- Max, 20.3.2015
        a = self.coerce_scalar_field(other)
        w = df.TestFunction(self.functionspace)
        v_res = df.assemble(df.dot(self.f * a.f, w) * df.dP)
        return Field(self.functionspace, value=v_res)

    def __rmul__(self, other):
        return self.__mul__(other)

    def __div__(self, other):
        # We use Claas Abert's 'point measure hack' for the vertex-wise operation.
        a = self.coerce_scalar_field(other)
        w = df.TestFunction(self.functionspace)
        v_res = df.assemble(df.dot(self.f / a.f, w) * df.dP)
        return Field(self.functionspace, value=v_res)

    def cross(self, other):
        """
        Return vector field representing the cross product of this field with `other`.
        """
        if not isinstance(other, Field):
            raise TypeError("Argument must be a Field. Got: {} ({})".format(other, type(other)))
        if not (self.value_dim() == 3 and other.value_dim() == 3):
            raise ValueError("The cross product is only defined for 3d vector fields.")
        # We use Claas Abert's 'point measure hack' for the vertex-wise cross product.
        w = df.TestFunction(self.functionspace)
        v_res = df.assemble(df.dot(df.cross(self.f, other.f), w) * df.dP)
        return Field(self.functionspace, value=v_res)

    def dot(self, other):
        """
        Return scalar field representing the dot product of this field with `other`.

        """
        if not isinstance(other, Field):
            raise TypeError("Argument must be a Field. Got: {} ({})".format(other, type(other)))
        if not (self.value_dim() == other.value_dim()):
            raise ValueError("The cross product is only defined for vector fields of the same dimension.")
        # We use Claas Abert's 'point measure hack' for the vertex-wise cross product.
        w = df.TestFunction(associated_scalar_space(self.functionspace))
        v_res = df.assemble(df.dot(df.dot(self.f, other.f), w) * df.dP)
        return self.coerce_scalar_field(v_res)

    def allclose(self, other, rtol=1e-7, atol=0):
        """
        Returns `True` if the two fields are element-wise equal up to the
        given tolerance.

        It compares the difference between 'self' and 'other' to
        `atol + rtol * abs(self)`

        This calls `np.allclose()` underneath, but with different
        default tolerances (in particular, we use atol=0 so that
        comparison also returns sensible results if the field values
        are very small numbers.

        The argument `other` must be either a scalar value or of type
        `Field`. Passing a numpy array raises an error because it is
        unclear in which order the values should be compares if the
        degrees of freedom of the underlying dolfin vector are
        re-ordered.

        """
        if not isinstance(other, Field):
            raise TypeError("Argument `other` must be of type'Field'. "
                            "Got: {} (type {}).".format(other, type(other)))

        a = other.f.vector().array()
        b = self.f.vector().array()

        return np.allclose(a, b, rtol=rtol, atol=atol)

    @property
    def np(self):
        if self.value_dim() == 1:
            # TODO: We should also rearrange these vector entries according to the dofmap.
            return self.get_ordered_numpy_array_xxx()
        elif self.value_dim() == 3:
            return self.get_ordered_numpy_array_xxx().reshape(3, -1)
        else:
            raise NotImplementedError("Numpy representation is only implemented for scalar and 3d vector fields.")

    def probe(self, coord):
        return self.f(coord)

    def mesh(self):
        return self.functionspace.mesh()

    def mesh_dim(self):
        return self.functionspace.mesh().topology().dim()

    def mesh_dofmap(self):
        return self.functionspace.dofmap()

    def value_dim(self):
        if self.is_scalar_field():
            # Scalar field.
            return 1
        else:
            # value_shape() returns a tuple (N,) and int is required.
            return self.functionspace.num_sub_spaces()#ufl_element().value_shape()[0]

    def vector(self):
        return self.f.vector()

    def petsc_vector(self):
        return df.as_backend_type(self.f.vector()).vec()

    def save_pvd(self, filename):
        """Save to pvd file using dolfin code"""
        if filename[-4:] != '.pvd':
            filename += '.pvd'
        pvd_file = df.File(filename)
        pvd_file << self.f

    def save_hdf5(self, filename, t):
        """
        Save field to h5 file and corresponding metadata (times at which field is saved),
        which is saved to a json file.

        Note, the mesh is automatically saved into this file as it is required by
        load_hdf5.

        Arguments:
        filename - filename of data to be saved (no extensions)

        t        - time at which the file is being save
                   it is recomended that this is taken from sim.t

        This function creates to files with filename.h5 and filename.json names.

        When simulation/field saving is finished, it is recomended that close_hdf5() is
        called.

        """
        # ask if file has already been created. If not, create it.
        if not hasattr(self, 'h5fileWrite'):
            self.h5fileWrite = dolfinh5tools.Create(filename, self.functionspace)
            self.h5fileWrite.save_mesh()
        self.h5fileWrite.write(self.f, self.name, t)

    def close_hdf5(self):
        """Close hdf5 file. Delete the saving object variable."""

        if hasattr(self, 'h5fileWrite'):
            self.h5fileWrite.close()
            del self.h5fileWrite

    def plot_with_dolfin(self, interactive=True):
        df.plot(self.f, interactive=interactive)

    def plot_with_paraview(self, **kwargs):
        """
        Render the field using Paraview and return an `IPython.display.Image`
        object with the resulting plot (which is displayed as a regular image
        in an IPython notebook). All keyword arguments are passed on to the
        function `finmag.util.visualization.render_paraview_scene`, which is
        used internally. This currently only works for 3D vector fields.

        """
        return plot_dolfin_function(self.f, **kwargs)

    def normalise_dofmap(self):
        """
        Overwrite own field values with normalised ones.

        """
        dofmap = df.vertex_to_dof_map(self.functionspace)
        reordered = self.f.vector().array()[dofmap]  # [x1, y1, z1, ..., xn, yn, zn]
        vectors = reordered.reshape((3, -1))  # [[x1, y1, z1], ..., [xn, yn, zn]]
        lengths = np.sqrt(np.add.reduce(vectors * vectors, axis=1))
        normalised = np.dot(vectors.T, np.diag(1 / lengths)).T.ravel()
        vertexmap = df.dof_to_vertex_map(self.functionspace)
        normalised_original_order = normalised[vertexmap]
        self.from_array(normalised_original_order)

    def normalise(self):
        """
        Normalises the Field, so that the norm at every mesh node is 1.
        """
        S1 = df.FunctionSpace(self.functionspace.mesh(), 'CG', 1)

        norm_squared = 0
        for i in range(self.value_dim()):
            norm_squared += self.f[i]*self.f[i]

        norm = df.Function(S1)
        norm_vector = df.assemble(df.dot(df.sqrt(norm_squared), df.TestFunction(S1))*df.dP)
        norm.vector().set_local(norm_vector.get_local())

        #self.f = df.project(self.f/norm, self.functionspace)
        self.f = (self / norm).f

    def get_spherical(self):
        """
        Transform magnetisation coordinates to spherical coordinates
        
        theta = arctan(m_r / m_z) ; m_r = sqrt(m_x ^ 2 + m_y ^ 2)
        phi = arctan(m_y / m_x)        

        The theta and phi generalised coordinates are stored in
        self.theta and self.phi respectively.
        
        When this function is called, the two dolfin functions
        are returned

        """

        # Create an scalar Function Space to compute the cylindrical radius (x^2 + y^2)
        # and the angles phi and theta
        S1 = df.FunctionSpace(self.functionspace.mesh(), 'CG', 1)

        # Create a dolfin function from the FS
        m_r = df.Function(S1)
        # Compute the radius using the assemble method with dolfin dP
        # (like a dirac delta to get values on every node of the mesh)
        # This returns a dolfin vector
        cyl_vector = df.assemble(df.dot(df.sqrt(self.f[0] * self.f[0] + self.f[1] * self.f[1]),
                                        df.TestFunction(S1)) * df.dP,
                         
                                 )
        # Set the vector values to the dolfin function
        m_r.vector().set_local(cyl_vector.get_local())

        # Now we compute the theta and phi angles to describe the magnetisation
        # and save them to the coresponding variables
        self.theta = df.Function(S1)
        self.phi = df.Function(S1)

        # We will use the same vector variable than the one used to
        # compute m_r,  in order to save memory

        # Theta = arctan(m_r / m_z)
        cyl_vector = df.assemble(df.dot(df.atan_2(m_r, self.f[2]),
                                        df.TestFunction(S1)) * df.dP,
                                 tensor=cyl_vector
                                 )

        # Instead of:
        # self.theta.vector().set_local(cyl_vector.get_local())
        # We will use:
        self.theta.vector().axpy(1, cyl_vector)
        # which adds:  1 * cyl_vector
        # to self.theta.vector() and is much faster
        # (we assume self.theta.vector() is empty, i.e. only made of zeros)
        # See: Fenics Book, page 44
 
        # Phi = arctan(m_y / m_x)
        cyl_vector = df.assemble(df.dot(df.atan_2(self.f[1], self.f[0]),
                                                df.TestFunction(S1)) * df.dP,
                                         tensor=cyl_vector
                                         )

        # We will save this line just in case:
        # self.phi.vector().set_local(cyl_vector.get_local())
        self.phi.vector().axpy(1, cyl_vector)

        return self.theta, self.phi
