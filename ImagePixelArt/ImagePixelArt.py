import os
import sys
import traceback
import math
import importlib

import adsk.core
import adsk.fusion

ADDIN_DIRECTORY = os.path.dirname(os.path.abspath(__file__))
if ADDIN_DIRECTORY not in sys.path:
    sys.path.insert(0, ADDIN_DIRECTORY)

import geometry
import png_reader
import image_processing


# Fusion keeps imported modules cached when an add-in is stopped and started again.
# Reload local modules so code updates cannot leave the entry point and helpers out of sync.
png_reader = importlib.reload(png_reader)
image_processing = importlib.reload(image_processing)
geometry = importlib.reload(geometry)

build_color_geometry = geometry.build_color_geometry
calculate_grid = geometry.calculate_grid
load_and_quantize = image_processing.load_and_quantize


APP = adsk.core.Application.get()
UI = APP.userInterface
COMMAND_ID = 'fusion_tools_image_pixel_art_v1'
COMMAND_NAME = 'Image to Pixel Art'
COMMAND_DESCRIPTION = 'Convert a raster image into merged pixel-art sketch profiles.'
PANEL_IDS = ('SolidCreatePanel', 'SketchCreatePanel')

_handlers = []
_active_state = None


class CommandState:
    def __init__(self, command: adsk.core.Command, design: adsk.fusion.Design):
        self.command = command
        self.design = design
        self.units_manager = design.unitsManager

    @property
    def inputs(self):
        return self.command.commandInputs

    def parse_length(self, input_id: str) -> float | None:
        command_input = adsk.core.StringValueCommandInput.cast(self.inputs.itemById(input_id))
        expression = command_input.value.strip()
        if not expression or not self.units_manager.isValidExpression(expression, 'mm'):
            return None
        value_cm = self.units_manager.evaluateExpression(expression, 'mm')
        return value_cm * 10.0 if value_cm > 0 else None

    def validate(self) -> tuple[bool, str]:
        image_input = adsk.core.StringValueCommandInput.cast(self.inputs.itemById('imagePath'))
        width_input = adsk.core.StringValueCommandInput.cast(self.inputs.itemById('width'))
        height_input = adsk.core.StringValueCommandInput.cast(self.inputs.itemById('height'))
        pixel_input = adsk.core.StringValueCommandInput.cast(self.inputs.itemById('pixelSize'))

        image_path = image_input.value.strip()
        image_valid = os.path.isfile(image_path) and os.path.splitext(image_path)[1].lower() == '.png'
        width_mm = self.parse_length('width')
        height_mm = self.parse_length('height')
        pixel_size_mm = self.parse_length('pixelSize')

        width_error = width_mm is None
        height_error = height_mm is None
        pixel_error = pixel_size_mm is None
        message = ''

        if not image_valid:
            message = 'Choose an existing PNG image.'
        elif width_error or height_error or pixel_error:
            message = 'Width, height, and pixel size must be positive length expressions.'
        else:
            width_ratio = width_mm / pixel_size_mm
            height_ratio = height_mm / pixel_size_mm
            width_error = not math.isclose(width_ratio, round(width_ratio), rel_tol=0, abs_tol=1e-7)
            height_error = not math.isclose(height_ratio, round(height_ratio), rel_tol=0, abs_tol=1e-7)

            if width_error or height_error:
                invalid_dimensions = []
                if width_error:
                    invalid_dimensions.append('width')
                if height_error:
                    invalid_dimensions.append('height')
                message = f'{" and ".join(invalid_dimensions).capitalize()} must be exactly divisible by the pixel size.'
            else:
                try:
                    columns, rows = calculate_grid(width_mm, height_mm, pixel_size_mm)
                    message = f'Grid: {columns} × {rows} = {columns * rows:,} pixels.'
                except ValueError as error:
                    message = str(error)
                    width_error = True
                    height_error = True

        image_input.isValueError = not image_valid
        width_input.isValueError = width_error
        height_input.isValueError = height_error
        pixel_input.isValueError = pixel_error

        status = adsk.core.TextBoxCommandInput.cast(self.inputs.itemById('status'))
        status.formattedText = message
        return image_valid and not width_error and not height_error and not pixel_error, message

    def select_image(self):
        dialog = UI.createFileDialog()
        dialog.title = 'Select a PNG image'
        dialog.filter = 'PNG images (*.png)'
        dialog.isMultiSelectEnabled = False

        if dialog.showOpen() == adsk.core.DialogResults.DialogOK:
            image_input = adsk.core.StringValueCommandInput.cast(self.inputs.itemById('imagePath'))
            image_input.value = dialog.filename

    def execute(self):
        is_valid, message = self.validate()
        if not is_valid:
            raise ValueError(message)

        image_path = adsk.core.StringValueCommandInput.cast(self.inputs.itemById('imagePath')).value.strip()
        width_mm = self.parse_length('width')
        height_mm = self.parse_length('height')
        pixel_size_mm = self.parse_length('pixelSize')
        color_count = adsk.core.IntegerSpinnerCommandInput.cast(self.inputs.itemById('colorCount')).value
        remove_small_regions = adsk.core.BoolValueCommandInput.cast(self.inputs.itemById('removeSmallRegions')).value
        columns, rows = calculate_grid(width_mm, height_mm, pixel_size_mm)
        pixel_image = load_and_quantize(image_path, columns, rows, color_count, remove_small_regions)
        geometry = build_color_geometry(pixel_image.pixels, pixel_image.palette)
        self.create_sketches(geometry, pixel_size_mm / 10.0)

        total_lines = sum(len(item.segments) for item in geometry)
        UI.messageBox(
            f'Created {len(geometry)} color sketches with {total_lines:,} boundary lines.\n'
            f'Grid: {columns} × {rows}; pixel size: {pixel_size_mm:g} mm.'
        )

    def create_sketches(self, geometry, pixel_size_cm: float):
        component = self.design.activeComponent or self.design.rootComponent
        plane = component.xYConstructionPlane

        for item in geometry:
            sketch = component.sketches.add(plane)
            red, green, blue = item.rgb
            sketch.name = f'Pixel Art {item.color_index + 1:02d} [#{red:02X}{green:02X}{blue:02X}]'
            sketch.isComputeDeferred = True
            lines = sketch.sketchCurves.sketchLines

            try:
                for segment in item.segments:
                    start = adsk.core.Point3D.create(segment.x1 * pixel_size_cm, segment.y1 * pixel_size_cm, 0)
                    end = adsk.core.Point3D.create(segment.x2 * pixel_size_cm, segment.y2 * pixel_size_cm, 0)
                    lines.addByTwoPoints(start, end)
            finally:
                sketch.isComputeDeferred = False


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global _active_state

        try:
            design = adsk.fusion.Design.cast(APP.activeProduct)
            if not design:
                UI.messageBox('Open or create a Fusion design before running Image to Pixel Art.')
                return

            command = args.command
            command.isExecutedWhenPreEmpted = False
            _active_state = CommandState(command, design)
            inputs = command.commandInputs

            image_input = inputs.addStringValueInput('imagePath', 'Image', '')
            image_input.tooltip = 'PNG image.'
            inputs.addBoolValueInput('browse', 'Browse…', False, '', False)
            inputs.addStringValueInput('width', 'Width', '100 mm')
            inputs.addStringValueInput('height', 'Height', '100 mm')
            inputs.addStringValueInput('pixelSize', 'Pixel size', '2 mm')
            inputs.addIntegerSpinnerCommandInput('colorCount', 'Colors', 2, 32, 1, 5)
            remove_small_regions = inputs.addBoolValueInput(
                'removeSmallRegions', 'Replace 1–2 pixel islands', True, '', True
            )
            remove_small_regions.tooltip = 'Replace tiny color islands using adjacent larger color regions.'
            status = inputs.addTextBoxCommandInput('status', 'Validation', 'Choose an image file.', 2, True)
            status.isFullWidth = True

            handler = InputChangedHandler()
            command.inputChanged.add(handler)
            _handlers.append(handler)

            handler = ValidateInputsHandler()
            command.validateInputs.add(handler)
            _handlers.append(handler)

            handler = ExecuteHandler()
            command.execute.add(handler)
            _handlers.append(handler)

            handler = DestroyHandler()
            command.destroy.add(handler)
            _handlers.append(handler)

            _active_state.validate()
        except:
            UI.messageBox('Image to Pixel Art failed to open:\n' + traceback.format_exc())


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        try:
            if not _active_state:
                return

            if args.input.id == 'browse':
                button = adsk.core.BoolValueCommandInput.cast(args.input)
                if button.value:
                    _active_state.select_image()
                    button.value = False

            _active_state.validate()
        except:
            APP.log(traceback.format_exc())


class ValidateInputsHandler(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        try:
            args.areInputsValid = bool(_active_state and _active_state.validate()[0])
        except:
            args.areInputsValid = False
            APP.log(traceback.format_exc())


class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            _active_state.execute()
        except:
            args.executeFailed = True
            args.executeFailedMessage = 'Could not create pixel-art sketches:\n' + traceback.format_exc()


class DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _active_state
        _active_state = None


def _find_panel():
    workspace = UI.workspaces.itemById('FusionSolidEnvironment')
    if not workspace:
        return None

    for panel_id in PANEL_IDS:
        panel = workspace.toolbarPanels.itemById(panel_id)
        if panel:
            return panel
    return None


def run(context):
    try:
        command_definition = UI.commandDefinitions.itemById(COMMAND_ID)
        if not command_definition:
            command_definition = UI.commandDefinitions.addButtonDefinition(
                COMMAND_ID, COMMAND_NAME, COMMAND_DESCRIPTION
            )

        handler = CommandCreatedHandler()
        command_definition.commandCreated.add(handler)
        _handlers.append(handler)

        panel = _find_panel()
        if panel and not panel.controls.itemById(COMMAND_ID):
            control = panel.controls.addCommand(command_definition)
            control.isPromoted = True
    except:
        UI.messageBox('Image to Pixel Art failed to start:\n' + traceback.format_exc())


def stop(context):
    global _active_state

    try:
        _active_state = None
        panel = _find_panel()
        if panel:
            control = panel.controls.itemById(COMMAND_ID)
            if control:
                control.deleteMe()

        command_definition = UI.commandDefinitions.itemById(COMMAND_ID)
        if command_definition:
            command_definition.deleteMe()
    except:
        APP.log(traceback.format_exc())
