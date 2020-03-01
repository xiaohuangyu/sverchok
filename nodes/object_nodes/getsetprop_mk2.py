# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import ast
import traceback

import bpy
from bpy.props import StringProperty, BoolProperty, IntProperty, FloatProperty, FloatVectorProperty
from bpy.types import bpy_prop_array
import mathutils
from mathutils import Matrix, Vector, Euler, Quaternion, Color

from sverchok.node_tree import SverchCustomTreeNode
from sverchok.data_structure import Matrix_generate, updateNode, node_id


def is_probably_color(item):
    if isinstance(item, bpy_prop_array):
        if hasattr(item, "path_from_id") and item.path_from_id().endswith('color'):
            return True


def parse_to_path(p):
    '''
    Create a path and can be looked up easily.
    Return an array of tuples with op type and value
    ops are:
    name - global name to use
    attr - attribute to get using getattr(obj,attr)
    key - key for accesing via obj[key]
    '''

    if isinstance(p, ast.Attribute):
        return parse_to_path(p.value)+[("attr", p.attr)] 
    elif isinstance(p, ast.Subscript):
        if isinstance(p.slice.value, ast.Num):
            return  parse_to_path(p.value) + [("key", p.slice.value.n)]
        elif isinstance(p.slice.value, ast.Str):
            return parse_to_path(p.value) + [("key", p.slice.value.s)]
    elif isinstance(p, ast.Name):
        return [("name", p.id)]
    else:
        raise NameError

def get_object(path):
    '''
    - access the object specified from a path generated by parse_to_path
    - this will fail if path is invalid
    '''
    curr_object = globals()[path[0][1]]
    for t, value in path[1:]:
        if t == "attr":
            curr_object = getattr(curr_object, value)
        elif t == "key":
            curr_object = curr_object[value]
    return curr_object

def apply_alias(eval_str):
    '''
    - apply standard aliases
    - will raise error if it isn't an bpy path
    '''
    if not eval_str.startswith("bpy."):
        for alias, expanded in aliases.items():
            if eval_str.startswith(alias):
                eval_str = eval_str.replace(alias, expanded, 1)
                break
        if not eval_str.startswith("bpy."):
            raise NameError
    return eval_str

def wrap_output_data(tvar):
    '''
    create valid sverchok socket data from an object
    '''
    if isinstance(tvar, Vector):
        data = [[tvar[:]]]
    elif isinstance(tvar, Color):
        data = [[Color(tvar)]]
    elif is_probably_color(tvar):
        # mathutils.Color is a 3 component object only. never 4. (2020-January)
        data = [[Color(tvar[:3])]]
    elif isinstance(tvar, Matrix):
        data = [[Matrix(tvar)]]
    elif isinstance(tvar, (Euler, Quaternion)):
        tvar = tvar.to_matrix().to_4x4()
        data = [[r[:] for r in tvar[:]]]
    elif isinstance(tvar, list):
        data = [tvar]
    elif isinstance(tvar, (int, float)):
        data = [[tvar]]
    else:
        data = tvar
    return data

def assign_data(obj, data):
    '''
    assigns data to the object
    '''
    if isinstance(obj, (int, float)):
        obj = data[0][0]
    elif isinstance(obj, (Vector, Color)):
        obj[:] = data[0][0] 
    elif isinstance(obj, (Matrix, Euler, Quaternion)):
        mat = data[0]
        if isinstance(obj, Euler):
            eul = mat.to_euler(obj.order)
            obj[:] = eul
        elif isinstance(obj, Quaternion):
            quat = mat.to_quaternion()
            obj[:] = quat 
        else: #isinstance(obj, Matrix)
            obj[:] = mat
    else: # super optimistic guess
        obj[:] = type(obj)(data[0][0])


aliases = {
    "c": "bpy.context",
    "C" : "bpy.context",
    "scene": "bpy.context.scene",
    "data": "bpy.data",
    "D": "bpy.data",
    "objs": "bpy.data.objects",
    "mats": "bpy.data.materials",
    "M": "bpy.data.materials",
    "meshes": "bpy.data.meshes",
    "texts": "bpy.data.texts"
}

types = {
    int: "SvStringsSocket",
    float: "SvStringsSocket",
    str: "SvStringsSocket",
    mathutils.Vector: "SvVerticesSocket",
    mathutils.Color: "SvColorSocket",
    mathutils.Matrix: "SvMatrixSocket",
    mathutils.Euler: "SvMatrixSocket",
    mathutils.Quaternion: "SvQuaternionSocket"
}

class SvPropNodeMixin():

    @property
    def obj(self):
        eval_str = apply_alias(self.prop_name)
        ast_path = ast.parse(eval_str)
        path = parse_to_path(ast_path.body[0].value)
        return get_object(path)
    
    def verify_prop(self, context):
        try:
            obj = self.obj
        except:
            traceback.print_exc()
            self.bad_prop = True
            return

        self.bad_prop = False
        with self.sv_throttle_tree_update():
            self.execute_inside_throttle()
        updateNode(self, context)
    
    def type_assesment(self):
        """
        we can use this function to perform more granular attr/type identification
        """
        item = self.obj
        s_type = types.get(type(item))
        if s_type:
            return s_type

        if is_probably_color(item):
            return "SvColorSocket"

        return None

    def prop_assesment(self):
        p_name = {
            float: "float_prop", 
            int: "int_prop",
            bpy_prop_array: "color_prop"
        }.get(type(self.obj),"")
        return p_name

    bad_prop: BoolProperty(default=False)
    prop_name: StringProperty(name='', update=verify_prop)


class SvGetPropNodeMK2(bpy.types.Node, SverchCustomTreeNode, SvPropNodeMixin):
    ''' Get property '''
    bl_idname = 'SvGetPropNodeMK2'
    bl_label = 'Get property MK2'
    bl_icon = 'FORCE_VORTEX'
    sv_icon = 'SV_PROP_GET'

    def execute_inside_throttle(self):    
        s_type = self.type_assesment()

        outputs = self.outputs
        if s_type and outputs:
            outputs[0].replace_socket(s_type)
        elif s_type:
            outputs.new(s_type, "Data")
    
    def draw_buttons(self, context, layout):
        layout.alert = self.bad_prop
        layout.prop(self, "prop_name", text="")

    def process(self):
        """ 
        convert path result to svdata for entering our nodetree 

        this is not updated in realtime, when you edit a property on f.ex "modifiers/count"
        requires a refresh of the tree to pick up current state.
        """
        self.outputs[0].sv_set(wrap_output_data(self.obj))


class SvSetPropNodeMK2(bpy.types.Node, SverchCustomTreeNode, SvPropNodeMixin):
    ''' Set property '''
    bl_idname = 'SvSetPropNodeMK2'
    bl_label = 'Set property MK2'
    bl_icon = 'FORCE_VORTEX'
    sv_icon = 'SV_PROP_SET'

    def local_updateNode(self, context):
        # no further interaction with the nodetree is required.
        self.process()

    float_prop: FloatProperty(update=local_updateNode, name="x")
    int_prop: IntProperty(update=local_updateNode, name="x")
    color_prop: FloatVectorProperty(
        name="Color", description="Color", size=3,
        min=0.0, max=1.0, subtype='COLOR', update=local_updateNode)

    def execute_inside_throttle(self):
        s_type = self.type_assesment()
        p_name = self.prop_assesment()

        inputs = self.inputs
        if inputs and s_type: 
            socket = inputs[0].replace_socket(s_type)
            socket.prop_name = p_name
        elif s_type:
            inputs.new(s_type, "Data").prop_name = p_name
        if s_type == "SvVerticesSocket":
            inputs[0].use_prop = True

    def draw_buttons(self, context, layout):
        layout.alert = self.bad_prop
        layout.prop(self, "prop_name", text="")

    def process(self):

        data = self.inputs[0].sv_get()
        eval_str = apply_alias(self.prop_name)
        ast_path = ast.parse(eval_str)
        path = parse_to_path(ast_path.body[0].value)
        obj = get_object(path)

        #with self.sv_throttle_tree_update():
            # changes here should not reflect back into the nodetree?

        try:
            if isinstance(obj, (int, float, bpy_prop_array)):
                obj = get_object(path[:-1])
                p_type, value = path[-1]
                if p_type == "attr":
                    setattr(obj, value, data[0][0])
                else: 
                    obj[value] = data[0][0]
            else:
                assign_data(obj, data)

        except Exception as err:
            print(err)



classes = [SvSetPropNodeMK2, SvGetPropNodeMK2]
register, unregister = bpy.utils.register_classes_factory(classes)
