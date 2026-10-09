# Shobotics

An experiment of curiosity.

Source repository for three Shobotics projects:

| Directory | Project |
| --- | --- |
| `svaram/` | Sonorium study forms, survey tooling, and the Polar H10 measurement rig. See [rig setup](svaram/svaram-h10/README.md). |
| `website/` | Brand assets and the Next.js website in `website/shobotics-site/`. See [website setup](website/shobotics-site/README.md). |
| `radioreport/` | Browser-based radiology report tooling. See [RadioReport setup](radioreport/README.md). |

Each project retains its own dependencies and run instructions. Run project commands from the relevant project directory.

## Repository scope

The root ignore rules include only these three project directories and repository documentation. Other local projects remain outside version control. Dependencies, generated app bundles, caches, environment secrets, and participant data exports are excluded. The Svaram project also excludes study databases, recordings, sessions, and identity records through its own ignore rules.

## Local Git history backups

When consolidating this workspace, the existing `svaram` and `radioreport` Git directories were preserved under `.repo-backups/` (ignored by this repository). The root repository tracks their current source files directly; earlier project commits remain in those local backups.
