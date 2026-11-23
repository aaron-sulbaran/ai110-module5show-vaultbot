---
title: Trailhead
type: project
---
# Trailhead

## Overview
Trailhead is an offline first hiking app. It downloads trail maps before a
hike and keeps working with no signal. It is the capstone project for the
spring semester, built with Sam Whitfield.

## Decisions
- Storage: SQLite on the device instead of Postgres on a server. The app has
  to work with no signal, and a server database cannot do that.
- Maps: vector tiles from OpenTrailMap, cached per region. Raster tiles were
  three times larger for the same area.
- Scope cut: no social feed in version one. Dr. Ortiz said it would not be
  graded and would cost two weeks.

## Timeline
- Proposal approved on March 4.
- Prototype demo is due April 17.
- Final presentation is May 8 in the engineering atrium.

## Open questions
- How large can a cached region get before older phones run out of space?
- Should elevation profiles be computed on the device or precomputed?
