# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EmailSorter is an auto email labeller that classifies Gmail and Outlook emails using an LLM layer, applies labels based on `categories.json`, and tracks run history in a metadata file.

## Email Classification

All categories and tags are defined in `categories.json`. The LLM prompt must use these definitions verbatim — do not hardcode category names or descriptions anywhere else in the code.

## LLM Layer

The LLM layer uses a strategy pattern so providers are interchangeable. The active provider is set via `LLM_PROVIDER` in `.env` (`gemini` or `local`). Adding a new provider means implementing the base interface — no changes to the categorizer or batch processor.

## Email Providers

Gmail and Outlook use an adapter pattern behind a common interface. Provider is selected via `EMAIL_PROVIDER` in `.env`. OAuth credentials and tokens are stored under `credentials/` (gitignored).

## Metadata & Batch Tracking

`metadata.json` records the last run timestamp, account used, and email date range covered. Batch mode stores processed email IDs so interrupted runs can resume without reprocessing.

## Run Modes

- **Default**: fetches emails after the last run date in `metadata.json`
- **Range**: accepts `--from` / `--to` date flags
- **Batch**: `--batch` fetches from the beginning in groups of 10, resumable via processed-ID tracking in `metadata.json`
