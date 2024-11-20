from inspect import stack
from math import degrees
from typing import cast, overload

from kipy.board import Board
from kipy.board_types import (
    Arc,
    Bezier,
    BoardLayer,
    ChamferedRectCorners,
    Circle,
    DrillShape,
    FootprintInstance,
    Pad,
    PadType,
    PadStackShape,
    Polygon,
    PolygonWithHoles,
    Rectangle,
    Segment,
    Shape,
)
from kipy.geometry import Angle, Box2, Vector2

from .common import EcadParser, Component, ExtraFieldData
from ..core.fontparser import FontParser



class KiCadIPCParser(EcadParser):

    def __init__(self, board: Board, config, logger):
        super(KiCadIPCParser, self).__init__(None, config, logger)
        self.board = board
        self.footprints = self.board.get_footprints()
        self.file_name = board.document.board_filename
        self.font_parser = FontParser()

    def get_extra_field_data(self, file_name):
        # TODO: implement
        return super().get_extra_field_data(file_name)

    @overload
    @staticmethod
    def normalize(point: Vector2) -> list[float]: ...

    @overload
    @staticmethod
    def normalize(point: int | float) -> float: ...

    @staticmethod
    def normalize(point: Vector2 | int | float):
        if isinstance(point, Vector2):
            return [point.x * 1e-6, point.y * 1e-6]
        else:
            return point * 1e-6

    @staticmethod
    def normalize_angle(angle):
        if isinstance(angle, int) or isinstance(angle, float):
            return angle * 0.1
        elif isinstance(angle, Angle):
            return angle.degrees
        else:
            return angle.AsDegrees()

    def parse_polygon(self, p: PolygonWithHoles):
        result = []
        for node in p.outline.nodes:
            if node.has_point:
                result.append(self.normalize(node.point))
            elif node.has_arc:
                # TODO: SWIG iteration gives you the arc approximation as points.
                # The IPC API shouldn't do this because we don't want approximation differences to
                # show up as changes in what the API delivers for a given board, so either the
                # clients need to start handling arcs or we need to put an arc approximation
                # mechanism into kicad-python?
                self.logger.warn("Arcs in polygons are not supported in IPC prototype")
        return result

    def parse_shape(self, d: Shape) -> dict | None:
        if isinstance(d, Segment):
            d = cast(Segment, d)
            return {
                "type": "segment",
                "start": self.normalize(d.start),
                "end": self.normalize(d.end),
                "width": self.normalize(d.attributes.stroke.width),
            }

        elif isinstance(d, Circle):
            d = cast(Circle, d)
            return {
                "type": "circle",
                "start": self.normalize(d.center),
                "radius": self.normalize(d.radius()),
                "width": self.normalize(d.attributes.stroke.width),
                "filled": int(d.attributes.fill.filled)
            }

        elif isinstance(d, Arc):
            d = cast(Arc, d)
            a1, a2 = d.start_angle(), d.end_angle()
            return {
                "type": "arc",
                "start": self.normalize(d.center()),
                "radius": self.normalize(d.radius()),
                "startangle": degrees(a1) if a1 is not None else None,
                "endangle": degrees(a2) if a2 is not None else None,
                "width": self.normalize(d.attributes.stroke.width),
            }

        elif isinstance(d, Polygon):
            d = cast(Polygon, d)
            shape_dict = {
                "type": "polygon",
                "pos": [0, 0],
                "angle": 0,
                "polygons": [self.parse_polygon(p) for p in d.polygons]
            }

            if not d.attributes.fill.filled:
                shape_dict["filled"] = 0
                shape_dict["width"] = self.normalize(d.attributes.stroke.width)
            return shape_dict

        elif isinstance(d, Bezier):
            d = cast(Bezier, d)
            return {
                "type": "curve",
                "start": self.normalize(d.start),
                "cpa": self.normalize(d.control1),
                "cpb": self.normalize(d.control2),
                "end": self.normalize(d.end),
                "width": self.normalize(d.attributes.stroke.width),
            }
        elif isinstance(d, Rectangle):
            d = cast(Rectangle, d)
            start = self.normalize(d.top_left)
            end = self.normalize(d.bottom_right)
            points = [
                start,
                [end[0], start[1]],
                end,
                [start[0], end[1]]
            ]
            return {
                "type": "polygon",
                "pos": [0, 0],
                "angle": 0,
                "polygons": [points],
                "width": self.normalize(d.attributes.stroke.width),
                "filled": int(d.attributes.fill.filled)
            }

        self.logger.info("Unsupported shape %s, skipping", type(d).__name__)
        return None

    def parse_edges(self, drawings):
        edges = []
        bbox = None
        for f in self.footprints:
            for g in f.definition.shapes():
                drawings.append(g)
        for d in drawings:
            if d.layer == BoardLayer.BL_Edge_Cuts:
                edges.append(self.parse_shape(d))
                if bbox is None:
                    bbox = d.bounding_box()
                else:
                    bbox.merge(d.bounding_box())
        # TODO(JE) needed?
        # if bbox:
        #     bbox.Normalize()
        return edges, bbox

    @staticmethod
    def footprint_to_component(footprint: FootprintInstance, extra_fields):
        footprint_name = str(footprint.definition.id)
        attr = 'Normal'

        if footprint.attributes.exclude_from_bill_of_materials:
            attr = 'Virtual'

        layer = {
            BoardLayer.BL_F_Cu: 'F',
            BoardLayer.BL_B_Cu: 'B',
        }.get(footprint.layer)

        return Component(footprint.reference_field.text.value,
                         footprint.value_field.text.value,
                         footprint_name,
                         layer,
                         attr,
                         extra_fields)

    @staticmethod
    def parse_chamfered_corners(corners: ChamferedRectCorners) -> int:
        ret = 0
        if corners.top_left:
            ret |= 1
        if corners.top_right:
            ret |= 2
        if corners.bottom_left:
            ret |= 4
        if corners.bottom_right:
            ret |= 8
        return ret

    def parse_pad(self, pad: Pad) -> dict | None:
        layers_set = pad.padstack.layers
        layers = []
        if BoardLayer.BL_F_Cu in layers_set:
            layers.append("F")
        if BoardLayer.BL_B_Cu in layers_set:
            layers.append("B")

        pos = self.normalize(pad.position)
        angle = self.normalize_angle(pad.padstack.angle)

        # Eventually would need to support full padstacks, but this is an API demo...
        stack_layer = pad.padstack.copper_layers[0]
        size = self.normalize(stack_layer.size)

        shape = {
            PadStackShape.PSS_CIRCLE: "circle",
            PadStackShape.PSS_OVAL: "oval",
            PadStackShape.PSS_RECTANGLE: "rect",
            PadStackShape.PSS_TRAPEZOID: "trapezoid",
            PadStackShape.PSS_ROUNDRECT: "roundrect",
            PadStackShape.PSS_CUSTOM: "custom",
            PadStackShape.PSS_CHAMFEREDRECT: "chamfrect",
        }.get(stack_layer.shape, "")

        if shape == "":
            self.logger.info("Unsupported pad shape %s, skipping.", stack_layer.shape)
            return None

        pad_dict = {
            "layers": layers,
            "pos": pos,
            "size": size,
            "angle": angle,
            "shape": shape
        }

        if shape == "custom":
            polygon = self.board.get_pad_shapes_as_polygons(pad)
            if polygon is not None:
                # get_pad_shapes_as_polygons returns absolute positions, but the function that
                # SWIG exposed that IBOM was using returned relative positions
                polygon.move(-pad.position)
                pad_dict["polygons"] = [self.parse_polygon(polygon),]
            else:
                pad_dict["polygons"] = []
                self.logger.warn('Custom pad shape could not be retrieved for pad %s', pad.number)
            pass
        if shape == "trapezoid":
            # treat trapezoid as custom shape
            pad_dict["shape"] = "custom"
            delta = self.normalize(stack_layer.trapezoid_delta)
            pad_dict["polygons"] = [[
                [size[0] / 2 + delta[1] / 2, size[1] / 2 - delta[0] / 2],
                [-size[0] / 2 - delta[1] / 2, size[1] / 2 + delta[0] / 2],
                [-size[0] / 2 + delta[1] / 2, -size[1] / 2 - delta[0] / 2],
                [size[0] / 2 - delta[1] / 2, -size[1] / 2 + delta[0] / 2],
            ]]

        if shape in ["roundrect", "chamfrect"]:
            pad_dict["radius"] = stack_layer.corner_rounding_ratio * min(size[0], size[1])
        if shape == "chamfrect":
            pad_dict["chamfpos"] = self.parse_chamfered_corners(stack_layer.chamfered_corners)
            pad_dict["chamfratio"] = stack_layer.chamfer_ratio

        if pad.pad_type in [PadType.PT_PTH, PadType.PT_NPTH]:
            pad_dict["type"] = "th"
            pad_dict["drillshape"] = {
                DrillShape.DS_CIRCLE: "circle",
                DrillShape.DS_OBLONG: "oblong"
            }.get(pad.padstack.drill.shape, "circle")
            pad_dict["drillsize"] = self.normalize(pad.padstack.drill.diameter)
        else:
            pad_dict["type"] = "smd"

        pad_dict["offset"] = self.normalize(stack_layer.offset)

        # TODO
        # if self.config.include_nets:
        #     pad_dict["net"] = pad.GetNetname()

        return pad_dict

    def parse_footprints(self):
        footprints = []
        for f in self.footprints:
            ref = f.reference_field.text

            # bounding box
            # TODO: add API call for bounding box
            footprint_rect = self.board.get_item_bounding_box(f)
            assert(footprint_rect)
            footprint_rect.move(-f.position)

            bbox = {
                "pos": self.normalize(f.position),
                "relpos": self.normalize(footprint_rect.pos),
                "size": self.normalize(footprint_rect.size),
                "angle": self.normalize_angle(f.orientation),
            }

            # graphical drawings
            drawings = []
            for d in f.definition.shapes():
                # we only care about copper ones, silkscreen is taken care of
                if d.layer not in [BoardLayer.BL_F_Cu, BoardLayer.BL_B_Cu]:
                    continue
                drawings.append({
                    "layer": "F" if d.layer == BoardLayer.BL_F_Cu else "B",
                    "drawing": self.parse_shape(d),
                })

            # footprint pads
            pads = []
            for p in f.definition.pads():
                pad_dict = self.parse_pad(p)
                if pad_dict is not None:
                    pads.append((p.number, pad_dict))

            if pads:
                # Try to guess first pin name.
                pads = sorted(pads, key=lambda el: el[0])
                pin1_pads = [p for p in pads if p[0] in
                             ['1', 'A', 'A1', 'P1', 'PAD1']]
                if pin1_pads:
                    pin1_pad_name = pin1_pads[0][0]
                else:
                    # No pads have common first pin name,
                    # pick lexicographically smallest.
                    pin1_pad_name = pads[0][0]
                for pad_name, pad_dict in pads:
                    if pad_name == pin1_pad_name:
                        pad_dict['pin1'] = 1

            pads = [p[1] for p in pads]

            # add footprint
            footprints.append({
                "ref": ref.value,
                "bbox": bbox,
                "pads": pads,
                "drawings": drawings,
                "layer": "F" if f.layer == BoardLayer.BL_F_Cu else "B"
            })

        return footprints

    def parse(self):
        from ..errors import ParsingException

        # TODO: extra field handling?

        # TODO: title block stuff
        title = ""
        revision = ""
        company = ""
        file_date = ""

        # TODO: this returns only shapes, not dimensions or text the way the old API does
        drawings = list(self.board.get_shapes())

        edges, bbox = self.parse_edges(drawings)
        if bbox is None:
            self.logger.error('Please draw pcb outline on the edges '
                              'layer on sheet or any footprint before '
                              'generating BOM.')
            return None, None
        bbox = {
            "minx": self.normalize(bbox.pos.x),
            "miny": self.normalize(bbox.pos.y),
            "maxx": self.normalize(bbox.pos.x + bbox.size.x),
            "maxy": self.normalize(bbox.pos.y + bbox.size.y),
        }

        pcbdata = {
            "edges_bbox": bbox,
            "edges": edges,
            "drawings": {
                "silkscreen": {
                    "F": [self.parse_shape(s) for s in drawings if s.layer == BoardLayer.BL_F_SilkS],
                    "B": [self.parse_shape(s) for s in drawings if s.layer == BoardLayer.BL_B_SilkS],
                },
                "fabrication": {
                    "F": [self.parse_shape(s) for s in drawings if s.layer == BoardLayer.BL_F_Fab],
                    "B": [self.parse_shape(s) for s in drawings if s.layer == BoardLayer.BL_B_Fab],
                }
            },
            "footprints": self.parse_footprints(),
            "metadata": {
                "title": title,
                "revision": revision,
                "company": company,
                "date": file_date,
            },
            "bom": {},
            "font_data": self.font_parser.get_parsed_font(),
        }
        #TODO(JE) not yet
        # if self.config.include_tracks:
        #     pcbdata["tracks"] = self.parse_tracks(self.board.get_tracks())
        #     pcbdata["zones"] = self.parse_zones(self.board.get_zones())
        # if self.config.include_nets:
        #     pcbdata["nets"] = self.parse_netlist(self.board.get_netlist())

        extra_fields = [{}] * len(self.footprints)

        components = [self.footprint_to_component(f, e)
                      for (f, e) in zip(self.footprints, extra_fields)]

        return pcbdata, components