# Repository Guidelines

## Scope

This repository contains independent tools and add-ins for Autodesk Fusion 360.

## Repository structure

- Keep each tool in its own top-level directory.
- Keep a tool's source code, manifest, assets, and documentation inside its directory.
- Add every new tool to the tool table in the root `README.md`.
- Do not mix implementation files from different tools.

## Documentation

- Write repository and tool documentation in English.
- Give each tool its own `README.md` with installation and usage instructions.
- Keep documented versions consistent with the corresponding manifest and source code.

## Changes

- Preserve existing behavior when importing an existing tool unless the task explicitly requests functional changes.
- Avoid unrelated fixes or refactoring.
- Preserve the existing style of each tool when making focused changes.
- Treat Fusion 360 API availability and behavior as runtime-dependent; do not claim full runtime verification unless the tool was tested in Fusion 360.

## Verification

- Validate Python syntax after changing Python files.
- Validate Fusion 360 manifest files as JSON after changing them.
- Check that README links and documented paths match the repository structure.
