from kipy.board import Board
from kipy.board_types import FootprintInstance, BoardLayer

from .common import EcadParser, Component, ExtraFieldData


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
    
    def parse_edges(self):
        edges = []
        drawings = list(self.board.get_shapes())
        bbox = None
        for f in self.footprints:
            for g in f.definition.shapes():
                drawings.append(g)
        for d in drawings:
            if d.layer == BoardLayer.BL_Edge_Cuts:
                for parsed_drawing in self.parse_drawing(d):
                    edges.append(parsed_drawing)
                    if bbox is None:
                        bbox = d.GetBoundingBox()
                    else:
                        bbox.Merge(d.GetBoundingBox())
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