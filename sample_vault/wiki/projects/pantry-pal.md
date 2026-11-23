---
title: Pantry Pal
type: project
---
# Pantry Pal

## Overview
Pantry Pal suggests recipes from ingredients already in the kitchen. It is a
weekend side project, not coursework.

## Decisions
- Recipes come from a fixed list of 200 recipes typed in by hand. Scraping
  recipe sites was rejected because of their terms of service.
- Matching is done by counting shared ingredients, then breaking ties by
  cooking time.

## Status
Paused since February. The ingredient scanner misreads handwritten labels,
and fixing that needs a better camera model.
