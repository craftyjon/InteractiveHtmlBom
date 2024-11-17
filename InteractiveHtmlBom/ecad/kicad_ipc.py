from kipy.board import Board
from kipy.board_types import (
    Arc,
    Bezier,
    BoardLayer,
    Circle,
    FootprintInstance,
    Polygon,
    Rectangle,
    Segment,
    Shape,
)
from kipy.geometry import Box2, Vector2

from .common import EcadParser, Component, ExtraFieldData
from math import degrees
from typing import cast

class KiCadIPCParser(EcadParser):

    def __init__(self, board: Board, config, logger):
        super(KiCadIPCParser, self).__init__(None, config, logger)
        self.board = board
        self.footprints = self.board.get_footprints()
        self.file_name = board.document.board_filename
        # self.font_parser = FontParser()

    def get_extra_field_data(self, file_name):
        # TODO: implement
        return super().get_extra_field_data(file_name)
    
    @staticmethod
    def normalize(point):
        if isinstance(point, Vector2):
            return [point.x * 1e-6, point.y * 1e-6]
        elif isinstance(point, int):
            return point * 1e-6

    @staticmethod
    def normalize_angle(angle):
        if isinstance(angle, int) or isinstance(angle, float):
            return angle * 0.1
        else:
            return angle.AsDegrees()
    
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
                "start": d.center,
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
                "angle": 0,
                "polygons": d.polygons
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
    
    def parse_edges(self):
        edges = []
        # TODO: this returns only shapes, not dimensions or text the way the old API does
        drawings = list(self.board.get_shapes())
        bbox = Box2()
        for f in self.footprints:
            for g in f.definition.shapes():
                drawings.append(g)
        for d in drawings:
            if d.layer == BoardLayer.BL_Edge_Cuts:
                for parsed_drawing in self.parse_shape(d):
                    edges.append(parsed_drawing)
                bbox.merge(d.bounding_box())
        if bbox:
            bbox.Normalize()
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

        return Component(footprint.reference_field.text,
                         footprint.value_field.text,
                         footprint_name,
                         layer,
                         attr,
                         extra_fields)
    
    def parse(self):
        from ..errors import ParsingException

        # TODO: extra field handling?

        # TODO: title block stuff

        edges, bbox = self.parse_edges()
        if bbox is None:
            self.logger.error('Please draw pcb outline on the edges '
                              'layer on sheet or any footprint before '
                              'generating BOM.')
            return None, None
        bbox = {
            "minx": bbox.GetPosition().x * 1e-6,
            "miny": bbox.GetPosition().y * 1e-6,
            "maxx": bbox.GetRight() * 1e-6,
            "maxy": bbox.GetBottom() * 1e-6,
        }

        drawings = self.get_all_drawings()

        pcbdata = {
            "edges_bbox": bbox,
            "edges": edges,
            "drawings": {
                "silkscreen": self.parse_drawings_on_layers(
                    drawings, pcbnew.F_SilkS, pcbnew.B_SilkS),
                "fabrication": self.parse_drawings_on_layers(
                    drawings, pcbnew.F_Fab, pcbnew.B_Fab),
            },
            "footprints": self.parse_footprints(),
            "metadata": {
                "title": title,
                "revision": revision,
                "company": company,
                "date": file_date,
            },
            "bom": {},
            "font_data": self.font_parser.get_parsed_font()
        }
        if self.config.include_tracks:
            pcbdata["tracks"] = self.parse_tracks(self.board.GetTracks())
            if hasattr(self.board, "Zones"):
                pcbdata["zones"] = self.parse_zones(self.board.Zones())
            else:
                self.logger.info("Zones not supported for KiCad 4, skipping")
                pcbdata["zones"] = {'F': [], 'B': []}
        if self.config.include_nets and hasattr(self.board, "GetNetInfo"):
            pcbdata["nets"] = self.parse_netlist(self.board.GetNetInfo())
    
        extra_fields = [{}] * len(self.footprints)

        components = [self.footprint_to_component(f, e)
                      for (f, e) in zip(self.footprints, extra_fields)]

        return pcbdata, components