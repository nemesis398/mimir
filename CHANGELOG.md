# Changelog

All notable changes to this plugin are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions
follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `scripts/config.py`, which reads and validates a vault's `.mimir.json`
  and names the key on any error.
- `scripts/notes.py`, which loads a vault's notes, skipping dot-directories,
  reads their frontmatter (flow and block lists alike) and extracts wikilink
  targets from property values.
