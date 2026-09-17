import adsk.core
import adsk.fusion
import traceback
import math
from dataclasses import dataclass

APP = adsk.core.Application.get()
UI = APP.userInterface

COMMAND_ID = 'la_pixel_brush_variable_size_v16'
COMMAND_NAME = 'Pixel Brush v1.6'
COMMAND_DESCRIPTION = 'Pixel Brush v1.6 — fixed per-pixel size, stable preview, hotkeys and Shift+wheel.'

_handlers = []
_state = None

# Coordinate quantization prevents floating-point noise from producing duplicate edges.
Q = 1_000_000


def q(v: float) -> int:
    return round(v * Q)


def uq(v: int) -> float:
    return v / Q


@dataclass(frozen=True)
class Pixel:
    # Coordinates and size are stored in Fusion internal length units (cm).
    x: float
    y: float
    size: float


class PixelBrushState:
    def __init__(self, sketch: adsk.fusion.Sketch):
        self.sketch = sketch
        self.pixels: set[Pixel] = set()
        self.current_pixel_size_cm = 0.2  # 2 mm
        self.mode = 'Draw'
        self.graphics_group = None
        self._last_signature = None
        self.shift_down = False
        self.command = None

    @property
    def component(self):
        return self.sketch.parentComponent

    def clear(self):
        self.pixels.clear()
        self._last_signature = None
        self.refresh_graphics()

    def end_stroke(self):
        self._last_signature = None

    def mouse_to_sketch_point(self, args):
        viewport = args.viewport
        pos = args.viewportPosition
        if not viewport or not pos:
            return None

        model_point = viewport.viewToModelSpace(pos)
        return self.sketch.modelToSketchSpace(model_point)

    def pixel_at_point(self, p):
        size = self.current_pixel_size_cm
        x = math.floor(p.x / size) * size
        y = math.floor(p.y / size) * size
        return Pixel(x, y, size)

    def paint_at(self, p):
        if self.mode == 'Erase':
            # Erasing is geometric: remove any existing pixel under the cursor,
            # regardless of the currently selected new-pixel size.
            victims = [
                px for px in self.pixels
                if px.x <= p.x < px.x + px.size
                and px.y <= p.y < px.y + px.size
            ]
            signature = ('erase', tuple(sorted((q(v.x), q(v.y), q(v.size)) for v in victims)))
            if signature == self._last_signature:
                return
            self._last_signature = signature

            if victims:
                for v in victims:
                    self.pixels.discard(v)
                self.refresh_graphics()
            return

        px = self.pixel_at_point(p)
        signature = ('draw', q(px.x), q(px.y), q(px.size))
        if signature == self._last_signature:
            return
        self._last_signature = signature

        if px not in self.pixels:
            self.pixels.add(px)
            self.refresh_graphics()

    def remove_graphics(self):
        if self.graphics_group and self.graphics_group.isValid:
            self.graphics_group.deleteMe()
        self.graphics_group = None

    def refresh_graphics(self):
        self.remove_graphics()

        if not self.pixels:
            APP.activeViewport.refresh()
            return

        group = self.component.customGraphicsGroups.add()
        group.name = 'Pixel Brush Preview'
        self.graphics_group = group

        coords = []
        indices = []

        for px in self.pixels:
            x0, y0 = px.x, px.y
            x1, y1 = px.x + px.size, px.y + px.size

            pts = [
                adsk.core.Point3D.create(x0, y0, 0),
                adsk.core.Point3D.create(x1, y0, 0),
                adsk.core.Point3D.create(x1, y1, 0),
                adsk.core.Point3D.create(x0, y1, 0),
            ]

            base = len(coords) // 3
            for p in pts:
                mp = self.sketch.sketchToModelSpace(p)
                coords.extend([mp.x, mp.y, mp.z])

            indices.extend([
                base, base + 1, base + 2,
                base, base + 2, base + 3
            ])

        cg_coords = adsk.fusion.CustomGraphicsCoordinates.create(coords)
        mesh = group.addMesh(cg_coords, indices, [], [])

        diffuse = adsk.core.Color.create(50, 130, 220, 255)
        ambient = adsk.core.Color.create(50, 130, 220, 255)
        specular = adsk.core.Color.create(255, 255, 255, 255)
        emissive = adsk.core.Color.create(0, 0, 0, 255)
        effect = adsk.fusion.CustomGraphicsBasicMaterialColorEffect.create(
            diffuse, ambient, specular, emissive, 20, 0.55
        )
        mesh.color = effect
        mesh.isSelectable = False
        APP.activeViewport.refresh()

    def generate_union_boundary(self):
        """
        Generates the boundary of the union of axis-aligned squares of arbitrary sizes.

        Algorithm:
        1. Collect every X/Y coordinate used by any square edge.
        2. This partitions the drawing into elementary rectangular cells.
        3. Mark each elementary cell as occupied if its center lies in any pixel.
        4. Emit only edges separating occupied and empty cells.
        5. Merge consecutive collinear edges.

        This supports mixing 1 mm, 2 mm, 5 mm, etc. pixels in the same drawing.
        """
        if not self.pixels:
            return

        xs = sorted({q(px.x) for px in self.pixels} |
                    {q(px.x + px.size) for px in self.pixels})
        ys = sorted({q(px.y) for px in self.pixels} |
                    {q(px.y + px.size) for px in self.pixels})

        occupied = set()

        # Mark elementary rectangles covered by at least one pixel.
        for ix in range(len(xs) - 1):
            cx = (uq(xs[ix]) + uq(xs[ix + 1])) / 2
            for iy in range(len(ys) - 1):
                cy = (uq(ys[iy]) + uq(ys[iy + 1])) / 2
                if any(
                    px.x <= cx < px.x + px.size and
                    px.y <= cy < px.y + px.size
                    for px in self.pixels
                ):
                    occupied.add((ix, iy))

        # Store segments by their constant coordinate.
        # Horizontal: y -> [(x0, x1), ...]
        # Vertical:   x -> [(y0, y1), ...]
        horizontal = {}
        vertical = {}

        def add_h(y, x0, x1):
            horizontal.setdefault(y, []).append((x0, x1))

        def add_v(x, y0, y1):
            vertical.setdefault(x, []).append((y0, y1))

        for ix, iy in occupied:
            x0, x1 = xs[ix], xs[ix + 1]
            y0, y1 = ys[iy], ys[iy + 1]

            if (ix, iy - 1) not in occupied:
                add_h(y0, x0, x1)
            if (ix, iy + 1) not in occupied:
                add_h(y1, x0, x1)
            if (ix - 1, iy) not in occupied:
                add_v(x0, y0, y1)
            if (ix + 1, iy) not in occupied:
                add_v(x1, y0, y1)

        def merge(intervals):
            intervals = sorted(intervals)
            if not intervals:
                return []

            out = []
            start, end = intervals[0]

            for a, b in intervals[1:]:
                if a <= end:
                    end = max(end, b)
                else:
                    out.append((start, end))
                    start, end = a, b

            out.append((start, end))
            return out

        lines = self.sketch.sketchCurves.sketchLines

        for y, intervals in horizontal.items():
            for x0, x1 in merge(intervals):
                lines.addByTwoPoints(
                    adsk.core.Point3D.create(uq(x0), uq(y), 0),
                    adsk.core.Point3D.create(uq(x1), uq(y), 0)
                )

        for x, intervals in vertical.items():
            for y0, y1 in merge(intervals):
                lines.addByTwoPoints(
                    adsk.core.Point3D.create(uq(x), uq(y0), 0),
                    adsk.core.Point3D.create(uq(x), uq(y1), 0)
                )


class CommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        global _state

        try:
            design = adsk.fusion.Design.cast(APP.activeProduct)
            sketch = adsk.fusion.Sketch.cast(design.activeEditObject) if design else None

            if not sketch:
                UI.messageBox(
                    'Pixel Brush works inside an active sketch.\n'
                    'Create or edit a sketch first.'
                )
                return

            _state = PixelBrushState(sketch)
            cmd = args.command
            _state.command = cmd
            inputs = cmd.commandInputs

            inputs.addTextBoxCommandInput(
                'versionInfo',
                'Version',
                '1.6 — stable preview + hotkeys',
                1,
                True
            )

            inputs.addValueInput(
                'pixelSize',
                'New pixel size',
                'mm',
                adsk.core.ValueInput.createByString('2 mm')
            )

            mode = inputs.addDropDownCommandInput(
                'mode',
                'Mode',
                adsk.core.DropDownStyles.TextListDropDownStyle
            )
            mode.listItems.add('Draw', True)
            mode.listItems.add('Erase', False)

            inputs.addBoolValueInput('clear', 'Clear pixels', False, '', False)

            h = InputChangedHandler()
            cmd.inputChanged.add(h)
            _handlers.append(h)

            h = MouseClickHandler()
            cmd.mouseClick.add(h)
            _handlers.append(h)

            h = MousePaintHandler()
            cmd.mouseDragBegin.add(h)
            _handlers.append(h)

            h = MousePaintHandler()
            cmd.mouseDrag.add(h)
            _handlers.append(h)

            h = MouseDragEndHandler()
            cmd.mouseDragEnd.add(h)
            _handlers.append(h)

            h = KeyDownHandler()
            cmd.keyDown.add(h)
            _handlers.append(h)

            h = KeyUpHandler()
            cmd.keyUp.add(h)
            _handlers.append(h)

            h = MouseWheelHandler()
            cmd.mouseWheel.add(h)
            _handlers.append(h)

            h = ExecuteHandler()
            cmd.execute.add(h)
            _handlers.append(h)

            h = DestroyHandler()
            cmd.destroy.add(h)
            _handlers.append(h)

        except:
            UI.messageBox('Pixel Brush failed:\n' + traceback.format_exc())


class InputChangedHandler(adsk.core.InputChangedEventHandler):
    def notify(self, args):
        global _state
        if not _state:
            return

        inp = args.input

        if inp.id == 'pixelSize':
            value_input = adsk.core.ValueCommandInput.cast(inp)
            if value_input.value > 0:
                # Only NEW pixels use this value.
                # Do NOT touch CustomGraphics here.
                # Existing pixels and their preview must remain exactly as they are.
                _state.current_pixel_size_cm = value_input.value
                _state.end_stroke()

        elif inp.id == 'mode':
            dd = adsk.core.DropDownCommandInput.cast(inp)
            if dd.selectedItem:
                _state.mode = dd.selectedItem.name
                _state.end_stroke()

        elif inp.id == 'clear':
            btn = adsk.core.BoolValueCommandInput.cast(inp)
            if btn.value:
                _state.clear()
                btn.value = False


class MouseClickHandler(adsk.core.MouseEventHandler):
    def notify(self, args):
        global _state
        try:
            if _state:
                p = _state.mouse_to_sketch_point(args)
                if p:
                    _state.paint_at(p)
                    _state.end_stroke()
        except:
            APP.log(traceback.format_exc())


class MousePaintHandler(adsk.core.MouseEventHandler):
    def notify(self, args):
        global _state
        try:
            if _state:
                p = _state.mouse_to_sketch_point(args)
                if p:
                    _state.paint_at(p)
        except:
            APP.log(traceback.format_exc())


class MouseDragEndHandler(adsk.core.MouseEventHandler):
    def notify(self, args):
        if _state:
            _state.end_stroke()


class KeyDownHandler(adsk.core.KeyboardEventHandler):
    STEP_CM = 0.1      # 1 mm
    MIN_SIZE_CM = 0.1  # 1 mm
    MAX_SIZE_CM = 10.0 # 100 mm

    # Fusion's keyCode is keyboard-layout/platform dependent.
    # These cover Qt-style arrow codes and common Windows virtual-key codes.
    UP_KEYS = {0x01000013, 38}
    DOWN_KEYS = {0x01000015, 40}

    # Common codes for +/-.
    PLUS_KEYS = {43, 61}
    MINUS_KEYS = {45}

    def notify(self, args):
        global _state

        if not _state or not _state.command:
            return

        try:
            key = args.keyCode
            modifiers = args.modifierMask

            shift_mask = adsk.core.KeyboardModifiers.ShiftKeyboardModifier
            shift = bool(modifiers & shift_mask)
            _state.shift_down = shift

            direction = 0

            # Primary hotkeys.
            if shift and key in self.UP_KEYS:
                direction = 1
            elif shift and key in self.DOWN_KEYS:
                direction = -1

            # Extra convenient hotkeys.
            elif key in self.PLUS_KEYS:
                direction = 1
            elif key in self.MINUS_KEYS:
                direction = -1

            if direction == 0:
                return

            new_size = _state.current_pixel_size_cm + direction * self.STEP_CM
            new_size = max(self.MIN_SIZE_CM, min(self.MAX_SIZE_CM, new_size))
            new_size = round(new_size, 6)

            if new_size == _state.current_pixel_size_cm:
                return

            # Update the UI field. inputChanged will then:
            # 1) update current_pixel_size_cm,
            # 2) redraw the preview,
            # 3) leave all old Pixel.size values untouched.
            size_input = adsk.core.ValueCommandInput.cast(
                _state.command.commandInputs.itemById('pixelSize')
            )

            if size_input:
                size_input.value = new_size
            else:
                _state.current_pixel_size_cm = new_size
                _state.end_stroke()

        except:
            APP.log(traceback.format_exc())


class KeyUpHandler(adsk.core.KeyboardEventHandler):
    def notify(self, args):
        global _state

        if not _state:
            return

        try:
            shift_mask = adsk.core.KeyboardModifiers.ShiftKeyboardModifier
            _state.shift_down = bool(args.modifierMask & shift_mask)
        except:
            _state.shift_down = False


class MouseWheelHandler(adsk.core.MouseEventHandler):
    STEP_CM = 0.1      # 1 mm
    MIN_SIZE_CM = 0.1  # 1 mm
    MAX_SIZE_CM = 10.0 # 100 mm

    def notify(self, args):
        global _state

        if not _state or not _state.command:
            return

        try:
            # Keep Shift+wheel as an alternative to keyboard hotkeys.
            # In the user's Fusion build wheelDelta is available and worked.
            modifiers = getattr(args, 'modifierMask', 0)
            shift_mask = adsk.core.KeyboardModifiers.ShiftKeyboardModifier
            shift = bool(modifiers & shift_mask)

            # Fallback: if modifierMask isn't exposed on this build,
            # rely on the last key state if present on state.
            if not shift and hasattr(_state, 'shift_down'):
                shift = bool(_state.shift_down)

            if not shift:
                return

            delta = getattr(args, 'wheelDelta', 0)
            if delta == 0:
                return

            direction = 1 if delta > 0 else -1

            new_size = _state.current_pixel_size_cm + direction * self.STEP_CM
            new_size = max(self.MIN_SIZE_CM, min(self.MAX_SIZE_CM, new_size))
            new_size = round(new_size, 6)

            if new_size == _state.current_pixel_size_cm:
                return

            size_input = adsk.core.ValueCommandInput.cast(
                _state.command.commandInputs.itemById('pixelSize')
            )

            if size_input:
                # This triggers inputChanged, which updates state and redraws preview.
                size_input.value = new_size
            else:
                _state.current_pixel_size_cm = new_size
                _state.end_stroke()

        except:
            APP.log(traceback.format_exc())


class ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _state
        try:
            if _state and _state.pixels:
                _state.remove_graphics()
                _state.generate_union_boundary()
        except:
            UI.messageBox('Could not generate sketch:\n' + traceback.format_exc())


class DestroyHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        global _state
        try:
            if _state:
                _state.remove_graphics()
        finally:
            _state = None


def run(context):
    try:
        command_def = UI.commandDefinitions.itemById(COMMAND_ID)
        if not command_def:
            command_def = UI.commandDefinitions.addButtonDefinition(
                COMMAND_ID, COMMAND_NAME, COMMAND_DESCRIPTION
            )

        h = CommandCreatedHandler()
        command_def.commandCreated.add(h)
        _handlers.append(h)

        workspace = UI.workspaces.itemById('FusionSolidEnvironment')
        panel = workspace.toolbarPanels.itemById('SketchCreatePanel')

        if panel and not panel.controls.itemById(COMMAND_ID):
            control = panel.controls.addCommand(command_def)
            control.isPromoted = True

    except:
        UI.messageBox('Pixel Brush failed to start:\n' + traceback.format_exc())


def stop(context):
    global _state

    try:
        if _state:
            _state.remove_graphics()
            _state = None

        workspace = UI.workspaces.itemById('FusionSolidEnvironment')
        panel = workspace.toolbarPanels.itemById('SketchCreatePanel')

        if panel:
            control = panel.controls.itemById(COMMAND_ID)
            if control:
                control.deleteMe()

        command_def = UI.commandDefinitions.itemById(COMMAND_ID)
        if command_def:
            command_def.deleteMe()

    except:
        APP.log(traceback.format_exc())
