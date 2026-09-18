# Image to Pixel Art for Fusion 360

## What it does

Image to Pixel Art converts a PNG image into Fusion 360 sketches:

- resizes the source to a user-defined pixel grid;
- reduces the image to 2–32 colors in the perceptual CIELAB color space without dithering;
- creates one sketch per color;
- optionally replaces isolated one- and two-pixel color regions;
- removes shared edges between adjacent pixels of the same color;
- merges consecutive collinear edges;
- preserves separate islands and holes;
- names each sketch with its palette color in hexadecimal form.

Transparent pixels are composited over white. The image is stretched to the requested width and height.

## Requirements

The add-in is self-contained and uses only Python's standard library and the Fusion API. No packages or separate applications need to be installed.

The built-in decoder supports standard PNG color formats, transparency, 1/2/4/8/16-bit samples, all PNG row filters, and non-interlaced or Adam7-interlaced images.

## Install

1. Copy or extract the `ImagePixelArt` directory.
2. In Fusion 360, open **Utilities → Add-Ins → Scripts and Add-Ins → Add-Ins**.
3. Click `+` and select the `ImagePixelArt` directory.
4. Run `ImagePixelArt`.
5. Open a design and run **Image to Pixel Art** from the Create panel.

After replacing add-in files with a newer version, stop and run the add-in again in **Scripts and Add-Ins**. Its local processing modules are reloaded automatically; restarting Fusion is not required.

## Usage

1. Select an image.
2. Enter the required width and height. Fusion length expressions such as `100 mm` and `4 in` are accepted.
3. Enter the exact square pixel size.
4. Choose the number of colors.
5. Leave **Replace 1–2 pixel islands** enabled to remove tiny color regions, or disable it to preserve every quantized pixel.
6. Click OK.

Width and height must both be exactly divisible by the pixel size. Invalid fields turn red, an explanation appears in the dialog, and OK remains disabled. For example, `100 × 60 mm` with a `2 mm` pixel creates a `50 × 30` grid.

The add-in limits the grid to 100,000 pixels and the generated result to 50,000 sketch lines. Increase the pixel size if either limit is reached.

For a one- or two-pixel color island, each pixel is evaluated independently against adjacent regions connected by a side. The replacement color is selected by the greatest number of touching sides, then by the largest connected region, and finally by the smallest RGB distance from the original color. Pixels without an adjacent region larger than two pixels are preserved.

Color quantization uses median-cut partitioning in CIELAB. Palette centers and nearest-color assignment use Lab coordinates and the CIE76 distance, which generally preserves visually distinct colors better than calculating distance directly in RGB.

## Output

The sketches are created on the active component's XY plane. If Fusion does not expose an active component, the root component is used as a fallback. A name such as `Pixel Art 01 [#E34234]` records the quantized RGB color; Fusion sketches themselves do not store fill colors.

## Runtime note

The image-processing and boundary algorithms are covered by local tests. Creation of actual sketches depends on the Fusion 360 runtime and must be verified in Fusion.
