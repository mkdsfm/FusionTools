# Fusion Tools

A collection of independent tools and add-ins for Autodesk Fusion 360.

Each tool lives in its own directory and includes dedicated installation and usage instructions.

## Tools

| Tool | Description | Version |
| --- | --- | --- |
| [Pixel Brush](./PixelBrush/) | A brush for painting square cells in Fusion 360 sketches and generating their combined boundary. | 1.6.0 |
| [Image to Pixel Art](./ImagePixelArt/) | Converts PNG images into color-separated pixel-art sketches with merged boundaries. | 1.0.0 |

## Installing add-ins

1. Download the repository or the directory of the tool you need.
2. In Fusion 360, open **Utilities → Add-Ins → Scripts and Add-Ins → Add-Ins**.
3. Click `+` and select the add-in directory.
4. Run the add-in.

See the README in each tool's directory for its specific requirements and instructions.

## Repository structure

```text
FusionTools/
├── README.md
└── <ToolName>/
    ├── <ToolName>.manifest
    ├── <ToolName>.py
    └── README.md
```

Add each new tool in a separate directory so its source code and documentation remain independent from other add-ins.
