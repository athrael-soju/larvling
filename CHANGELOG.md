# Changelog

## [0.1.36](https://github.com/athrael-soju/larvling/compare/v0.1.35...v0.1.36) (2026-10-21)


### Bug Fixes

* run extraction in an isolated session and stop false recording warnings ([#85](https://github.com/athrael-soju/larvling/issues/85)) ([3fdce45](https://github.com/athrael-soju/larvling/commit/3fdce45c9896d3350f91a4973cbf82da8d5e3812))

## [0.1.35](https://github.com/athrael-soju/larvling/compare/v0.1.34...v0.1.35) (2026-10-21)


### Features

* Phase 1 extraction integrity ([#83](https://github.com/athrael-soju/larvling/issues/83)) ([2397d13](https://github.com/athrael-soju/larvling/commit/2397d1375375d50d993c38ec95c285ec85cb133d))

## [0.1.34](https://github.com/athrael-soju/larvling/compare/v0.1.33...v0.1.34) (2026-06-24)


### Features

* implement recording health checks and user notifications for silent failures ([da3482e](https://github.com/athrael-soju/larvling/commit/da3482e32e20f94430fad39ab19e93e424cbf43c))

## [0.1.33](https://github.com/athrael-soju/larvling/compare/v0.1.32...v0.1.33) (2026-06-01)


### Bug Fixes

* list the now-horizon in full at session start ([9276daa](https://github.com/athrael-soju/larvling/commit/9276daa881704926cdd4b35add06558b015ab2f8))
* size query results to the question, not a reflexive LIMIT ([75c27b9](https://github.com/athrael-soju/larvling/commit/75c27b9d2135a7c0273adb290dd7e4e3a2d946d5))

## [0.1.32](https://github.com/athrael-soju/larvling/compare/v0.1.31...v0.1.32) (2026-06-01)


### Bug Fixes

* bound SessionStart open-task injection to a briefing ([f4d0757](https://github.com/athrael-soju/larvling/commit/f4d07574fa9742b33e39141784ad309fbbc6a585))
* make dropped/done tasks tombstones to stop resurrection ([ff021ac](https://github.com/athrael-soju/larvling/commit/ff021ac54aad142083fbeef573e8fe4ad3f7d02b))

## [0.1.31](https://github.com/athrael-soju/larvling/compare/v0.1.30...v0.1.31) (2026-06-01)


### Features

* refine SQL query guidance to emphasize scoping and error handling for oversized results ([edc2a36](https://github.com/athrael-soju/larvling/commit/edc2a36dacd26179347c8bfc70d909f7af6392b1))

## [0.1.30](https://github.com/athrael-soju/larvling/compare/v0.1.29...v0.1.30) (2026-06-01)


### Features

* enhance SQL query guidance with best practices for result shaping and aggregation ([05f1cdf](https://github.com/athrael-soju/larvling/commit/05f1cdf1ff7ae05493654703051eb1777bece1d7))

## [0.1.29](https://github.com/athrael-soju/larvling/compare/v0.1.28...v0.1.29) (2026-06-01)


### Features

* improve SQL query output handling with enhanced size limits and formatting ([050a3a2](https://github.com/athrael-soju/larvling/commit/050a3a2ec57903f27801ebf18bb1cd59bd65a786))

## [0.1.28](https://github.com/athrael-soju/larvling/compare/v0.1.27...v0.1.28) (2026-06-01)


### Features

* enhance query output formatting with cell truncation and whitespace handling ([933713a](https://github.com/athrael-soju/larvling/commit/933713ae71657e147fbbb4bf1b6110f82ec68324))

## [0.1.27](https://github.com/athrael-soju/larvling/compare/v0.1.26...v0.1.27) (2026-06-01)


### Features

* enhance knowledge context handling in prompts with improved recall guidance ([610f2b6](https://github.com/athrael-soju/larvling/commit/610f2b6d4cec19af2a0478e765bbd9c10c04f9d1))

## [0.1.26](https://github.com/athrael-soju/larvling/compare/v0.1.25...v0.1.26) (2026-04-24)


### Features

* update database schema to use CURRENT_TIMESTAMP for default timestamps ([81c0981](https://github.com/athrael-soju/larvling/commit/81c0981b682a6fd7883ffe7d8f825e121108a81e))

## [0.1.25](https://github.com/athrael-soju/larvling/compare/v0.1.24...v0.1.25) (2026-04-23)


### Features

* update task schema to include 'updated' timestamp and JSON metadata for provenance ([304d37c](https://github.com/athrael-soju/larvling/commit/304d37c2f8225a350ea347408c82e9fb4164ca7f))

## [0.1.24](https://github.com/athrael-soju/larvling/compare/v0.1.23...v0.1.24) (2026-04-22)


### Features

* update knowledge maintenance process and introduce tidy skill for auditing ([11a60f5](https://github.com/athrael-soju/larvling/commit/11a60f5d2364be7ba510d90a521c1ed388bd2b35))

## [0.1.23](https://github.com/athrael-soju/larvling/compare/v0.1.22...v0.1.23) (2026-04-01)


### Features

* enhance documentation and improve session management features ([b1836ee](https://github.com/athrael-soju/larvling/commit/b1836ee62d68f47369a106ea681d0e83c9386a53))

## [0.1.22](https://github.com/athrael-soju/larvling/compare/v0.1.21...v0.1.22) (2026-04-01)


### Bug Fixes

* update Python executable references in documentation and scripts ([17968c2](https://github.com/athrael-soju/larvling/commit/17968c2f0fa014122a5a9ad666efa393f503da86))

## [0.1.21](https://github.com/athrael-soju/larvling/compare/v0.1.20...v0.1.21) (2026-04-01)


### Bug Fixes

* improve formatting in README for skills and files sections ([eeb5e26](https://github.com/athrael-soju/larvling/commit/eeb5e2625941375ab4b62491f2ffc6d54562f434))

## [0.1.20](https://github.com/athrael-soju/larvling/compare/v0.1.19...v0.1.20) (2026-04-01)


### Bug Fixes

* improve Python version check and enhance dependency installation error handling ([80cc024](https://github.com/athrael-soju/larvling/commit/80cc024713bec9063abe7a0ef78ad9784d273dab))

## [0.1.19](https://github.com/athrael-soju/larvling/compare/v0.1.18...v0.1.19) (2026-04-01)


### Bug Fixes

* enhance dependency check and installation for claude-agent-sdk ([ec7ed82](https://github.com/athrael-soju/larvling/commit/ec7ed82df5855f0858f08bd922bed6e5632c6a03))

## [0.1.18](https://github.com/athrael-soju/larvling/compare/v0.1.17...v0.1.18) (2026-03-31)


### Bug Fixes

* add completion instructions for knowledge maintenance and summary agents ([66599f6](https://github.com/athrael-soju/larvling/commit/66599f6d27604ca6aaea4fb7b40f0ea13d6a7955))

## [0.1.17](https://github.com/athrael-soju/larvling/compare/v0.1.16...v0.1.17) (2026-03-31)


### Bug Fixes

* update documentation and scripts to use 'python' instead of 'python3' ([2a17760](https://github.com/athrael-soju/larvling/commit/2a17760fb5fb7450a8ce70d2b4d7306c98b7dca3))

## [0.1.16](https://github.com/athrael-soju/larvling/compare/v0.1.15...v0.1.16) (2026-03-30)


### Bug Fixes

* update script to use dynamic Python executable for command execution ([9d44680](https://github.com/athrael-soju/larvling/commit/9d44680653c5884f97792e879c365326868167fa))

## [0.1.15](https://github.com/athrael-soju/larvling/compare/v0.1.14...v0.1.15) (2026-03-30)


### Bug Fixes

* update command execution to use dynamic Python version ([66612ad](https://github.com/athrael-soju/larvling/commit/66612ad34e6eaf1450a9704b8001bd7c10bd7e15))

## [0.1.14](https://github.com/athrael-soju/larvling/compare/v0.1.13...v0.1.14) (2026-03-22)


### Bug Fixes

* classify task-notification messages as system role ([4202b9b](https://github.com/athrael-soju/larvling/commit/4202b9b98bae806c6a6be69c48cf9c21796f7167))

## [0.1.13](https://github.com/athrael-soju/larvling/compare/v0.1.12...v0.1.13) (2026-03-22)


### Bug Fixes

* use python3 instead of python in all hooks and scripts ([0b80d7e](https://github.com/athrael-soju/larvling/commit/0b80d7ead428b1e536daf8a74179dd0d8b524675))
* use python3 instead of python in all hooks, scripts, and docs ([ec386fe](https://github.com/athrael-soju/larvling/commit/ec386fe80769d2743cc7530ef7df5bb4b0033338))

## [0.1.12](https://github.com/athrael-soju/larvling/compare/v0.1.11...v0.1.12) (2026-03-05)


### Features

* add dependency check for claude_agent_sdk in preflight.py and update troubleshooting instructions in CLAUDE.md ([16ef6c9](https://github.com/athrael-soju/larvling/commit/16ef6c9f32dc79fa32a1dd1544ce7fc61cc91640))


### Documentation

* add troubleshooting section for Python command issues in README.md and CLAUDE.md ([c6d5928](https://github.com/athrael-soju/larvling/commit/c6d592896513eadce347a946d7925bbad3f56785))

## [0.1.11](https://github.com/athrael-soju/larvling/compare/v0.1.10...v0.1.11) (2026-03-02)


### Refactoring

* improve formatting and structure of validation constants and extraction phases in analyze.py ([7aafd72](https://github.com/athrael-soju/larvling/commit/7aafd72586555846fba6784a1c06fae2a081e535))


### Documentation

* add quick column reference for database tables in CLAUDE.md ([55aa1c8](https://github.com/athrael-soju/larvling/commit/55aa1c8e3e7a1d02599f157cd4ff69b0783b32b8))

## [0.1.10](https://github.com/athrael-soju/larvling/compare/v0.1.9...v0.1.10) (2026-02-28)


### Features

* configuration management and query enhancements ([bde724d](https://github.com/athrael-soju/larvling/commit/bde724d731ae6bf2f1460a442e54c52ca8193ff9))

## [0.1.9](https://github.com/athrael-soju/Larvling/compare/v0.1.8...v0.1.9) (2026-02-27)


### Refactoring

* Enhance knowledge extraction and task management processes with improved logging and validation ([06f3796](https://github.com/athrael-soju/Larvling/commit/06f37965b0028ee937a3996a94549712c7e56a7a))
* Simplify knowledge search queries and enhance task processing logic ([2591e03](https://github.com/athrael-soju/Larvling/commit/2591e036a59c7dad9b7f5e62e36f94c80cbe76b3))
* Update extraction logic and enhance session summary limits ([f928174](https://github.com/athrael-soju/Larvling/commit/f928174eb0215c9ac1e51352f3a66cc73cd1526a))

## [0.1.8](https://github.com/athrael-soju/Larvling/compare/v0.1.7...v0.1.8) (2026-02-27)


### Refactoring

* Enhance knowledge deduplication process in analyze.py and update CLAUDE.md ([800a8b1](https://github.com/athrael-soju/Larvling/commit/800a8b1254b1d14969c89f4787adc93870e36ee5))
* Remove token tracking and improve knowledge dedup ([b003df9](https://github.com/athrael-soju/Larvling/commit/b003df9a1a29d89d208c51328d5e0397065ecd99))
* Remove token usage tracking and related metadata from hooks and analysis ([a6f3f5d](https://github.com/athrael-soju/Larvling/commit/a6f3f5d50a8d9ac707fc7ea9c95d0ed5f2b35f19))
* Remove token usage tracking from stop and extract hooks in README ([1f89cc6](https://github.com/athrael-soju/Larvling/commit/1f89cc6dc606d85d0901c6967fbe7f0de7398005))
* Update README to remove token usage from session tracking description ([03a6fa0](https://github.com/athrael-soju/Larvling/commit/03a6fa015062aed5751237be4a70b8dd001b807d))

## [0.1.7](https://github.com/athrael-soju/Larvling/compare/v0.1.6...v0.1.7) (2026-02-26)


### Features

* Add detailed SQLite migration rules for schema updates ([1284209](https://github.com/athrael-soju/Larvling/commit/12842093db501a76ec4cf72830a73e657c01565b))
* Enhance session end handling to log ghost sessions ([87331e0](https://github.com/athrael-soju/Larvling/commit/87331e0b8e45c5357458ec02bdb3fbeefebac64a))

## [0.1.6](https://github.com/athrael-soju/Larvling/compare/v0.1.5...v0.1.6) (2026-02-26)


### Features

* Add knowledge maintenance capabilities, including audit and consolidation of the knowledge base; update documentation and settings ([38967e3](https://github.com/athrael-soju/Larvling/commit/38967e3c038b1fad4ab2b3c77268bb307b0c5c8b))
* Add PreCompact hook for auto-generating session summaries before compaction ([6da2904](https://github.com/athrael-soju/Larvling/commit/6da2904ae2f7a1bc195c7f9e510606f31aafcbc7))
* Add summary-manager configuration to settings.json for enhanced summary management ([54ce2cd](https://github.com/athrael-soju/Larvling/commit/54ce2cda5ccd19971ecb59fc459b67eecfb8ba87))
* Enhance Larvling schema, add unified analysis script, and improve hook functionality ([66edb6d](https://github.com/athrael-soju/Larvling/commit/66edb6d13b7a5ba6072ed20ed34cf79fd4ec3311))
* Introduce knowledge management system ([37a54dc](https://github.com/athrael-soju/Larvling/commit/37a54dc1d4e253a484af5e6fdf681ac49ef3f73e))
* Refactor domain, priority, and horizon constants for improved clarity and organization ([69587ba](https://github.com/athrael-soju/Larvling/commit/69587bad374bbc292e6a5399c7cc5f5301df0926))
* Refactor session summarization process, remove PreCompact hook, and enhance summary management ([81c250c](https://github.com/athrael-soju/Larvling/commit/81c250c154e822c04a9809688d1b9fb71dd916c8))
* Remove dashboard template, update schema documentation, and clean up quality signal handling ([64c4073](https://github.com/athrael-soju/Larvling/commit/64c4073fbcde388d6226649a05760683b53cf3b5))
* Update analysis script to remove sentiment extraction and adjust schema requirements ([703adbc](https://github.com/athrael-soju/Larvling/commit/703adbc217e581b0ffe147ee47c58f0f6795de32))
* Update diagram to reflect new schema and capabilities ([fe2356a](https://github.com/athrael-soju/Larvling/commit/fe2356a714973ad3f4c93d6e0bcd8c1959748e7e))
* Update knowledge management terminology and schema for clarity and consistency ([c0ffe69](https://github.com/athrael-soju/Larvling/commit/c0ffe698347c14f2fc1943a311d26a3ee95a1ce7))
* Update Larvling schema and capabilities, enhance documentation, and remove dashboard generation script ([afb9d9a](https://github.com/athrael-soju/Larvling/commit/afb9d9ab3d2ad13df754a493b2b7ba7d03a5e29e))
* Update plugin manifest, add changelog, license, and README; enhance session management and caching ([1be915e](https://github.com/athrael-soju/Larvling/commit/1be915e3b330fa91f54984040b6e0fb1b7425ba6))
* Update remember and summarize skills to enhance knowledge storage and session summary generation ([ab4d60c](https://github.com/athrael-soju/Larvling/commit/ab4d60cb18e8408a380975940673370cffb81dd1))


### Bug Fixes

* Address PR review findings — NameError in detached payload, domain validation, variable scoping ([6651c16](https://github.com/athrael-soju/Larvling/commit/6651c16c9e8b9e93febfa1180ae669668dd0effa))

## [0.1.5](https://github.com/athrael-soju/Larvling/compare/v0.1.4...v0.1.5) (2026-02-24)


### Features

* enhance logging and data structure for improved token tracking and analysis ([ed649fd](https://github.com/athrael-soju/Larvling/commit/ed649fd4b356d30879357248acdb7c071733af7b))
* enhance token tracking and logging across hooks and scripts ([1aca07c](https://github.com/athrael-soju/Larvling/commit/1aca07c9315c792a66b879a7e9bfc8d0e7fd2ec9))
* enhance token tracking and usage metrics across hooks and scripts ([e3640b4](https://github.com/athrael-soju/Larvling/commit/e3640b4f39dfc22ab1260bf7577594bd3fdaa967))
* update context injection and logging for improved token analysis ([d4f4da2](https://github.com/athrael-soju/Larvling/commit/d4f4da2b2d8cee8f47e1423a40e93a41a541e613))


### Bug Fixes

* change hook type from intercept to command for session start and user prompt submit ([d3febc0](https://github.com/athrael-soju/Larvling/commit/d3febc0ae5293d4c056e27f8904ca39c7dbcce4a))


### Refactoring

* remove redundant logging for database changes in extraction process ([998d0cb](https://github.com/athrael-soju/Larvling/commit/998d0cb7796df823e2420e202aa12f3832fd3499))

## [0.1.4](https://github.com/athrael-soju/Larvling/compare/v0.1.3...v0.1.4) (2026-02-24)


### Features

* Add graph refresh functionality and update dashboard generation modes ([a095891](https://github.com/athrael-soju/Larvling/commit/a095891f51ac99a749a741a7f5a3439fa6a84fa8))
* Enhance dashboard with graph statistics, curved edges, and improved node interactions ([628e803](https://github.com/athrael-soju/Larvling/commit/628e8038d0e0e42189ed622271e89dccfea144bf))
* Enhance quality signal management and refactor hook payload handling ([b0aeb58](https://github.com/athrael-soju/Larvling/commit/b0aeb587f151f74d4222dbcef5e9086966340d63))
* Implement template fetching and caching for dashboard rendering ([f950714](https://github.com/athrael-soju/Larvling/commit/f950714aa0ec4f9afd4d2a227f8863ee6613f726))
* Introduce Fact Graph agent and update dashboard to reflect changes ([8f9c27d](https://github.com/athrael-soju/Larvling/commit/8f9c27d52e6116be16a7a5ed0997d25418dfbfb2))
* modernize plugin structure ([eb52869](https://github.com/athrael-soju/Larvling/commit/eb5286902cccd3f889f9ed1d84dd4bbfdb459d6b))
* modernize plugin structure with skills, agents, hooks, and output styles ([b2490bc](https://github.com/athrael-soju/Larvling/commit/b2490bc44d39e7d76b525f2e3ca4cb50ddb16e0a))
* Refactor dashboard rendering logic and remove unused graph data handling ([6130f40](https://github.com/athrael-soju/Larvling/commit/6130f408528a8f8a39436fa48c8637a7db4abb3c))
* Remove auto-summarization script and update session end hook command ([52bc00d](https://github.com/athrael-soju/Larvling/commit/52bc00d51dff1e8116c2852fdabc62d02ccda866))
* Remove Fact Graph agent and related functionality from dashboard generation ([d77c11b](https://github.com/athrael-soju/Larvling/commit/d77c11bf2f91d814001c441485b6b1368b29e822))
* Update Larvling dashboard with Knowledge Graph and improve hooks ([26945f7](https://github.com/athrael-soju/Larvling/commit/26945f75562a8d5856f80c972291d6bea489dda8))
* Update README to reflect changes in command terminology and enhance dashboard description ([08d3614](https://github.com/athrael-soju/Larvling/commit/08d3614e9d1bac2726b8188be0ee5a5baa90886d))
* Update README to reflect on-demand dashboard generation and script modifications ([0a6c678](https://github.com/athrael-soju/Larvling/commit/0a6c6789996c43b15107e475af3e77439d0fe07a))
* Update terminology from 'commands' to 'skills' in documentation and precompact script ([47ef3f8](https://github.com/athrael-soju/Larvling/commit/47ef3f8dc8c921873740817841ef5e225bc84b7c))


### Bug Fixes

* add explicit AskUserQuestion usage to remember skill and fact-manager agent ([00553f6](https://github.com/athrael-soju/Larvling/commit/00553f6dde565cd50a107afa821092f330b74aca))
* Correct removal command in README for Larvling data files ([e34c049](https://github.com/athrael-soju/Larvling/commit/e34c049b99519ab962ef0fd89c3b4580bffddd1d))
* make PostToolUseFailure hook synchronous for reliable stdin delivery ([199cb76](https://github.com/athrael-soju/Larvling/commit/199cb76ba7ce31d8163009fd921441f1e1b7aec5))
* Revert version number to 0.1.3 and update changelog header image URL ([47fbd40](https://github.com/athrael-soju/Larvling/commit/47fbd4090e9cf5c4147bdf404ac9027a9f1c745e))


### Refactoring

* Improve clarity in README by updating descriptions and correcting image alt text ([85602d4](https://github.com/athrael-soju/Larvling/commit/85602d4663a37d12c256c392ef9fbdb693a10486))
* Update preflight script for schema bootstrap and enhance session start hook integration ([ef67ea5](https://github.com/athrael-soju/Larvling/commit/ef67ea544b8f5c7bedd9d9493f507a95756cb7e0))
* Update README to improve clarity and organization of principles and skills ([c67c079](https://github.com/athrael-soju/Larvling/commit/c67c079f37ca50d846d39283b44ca2628bdbdeb3))

## [0.1.4](https://github.com/athrael-soju/Larvling/compare/v0.1.3...v0.1.4) (2026-02-23)


### Features

* Add graph refresh functionality and update dashboard generation modes ([a095891](https://github.com/athrael-soju/Larvling/commit/a095891f51ac99a749a741a7f5a3439fa6a84fa8))
* Enhance dashboard with graph statistics, curved edges, and improved node interactions ([628e803](https://github.com/athrael-soju/Larvling/commit/628e8038d0e0e42189ed622271e89dccfea144bf))
* Implement template fetching and caching for dashboard rendering ([f950714](https://github.com/athrael-soju/Larvling/commit/f950714aa0ec4f9afd4d2a227f8863ee6613f726))
* Introduce Fact Graph agent and update dashboard to reflect changes ([8f9c27d](https://github.com/athrael-soju/Larvling/commit/8f9c27d52e6116be16a7a5ed0997d25418dfbfb2))
* modernize plugin structure ([eb52869](https://github.com/athrael-soju/Larvling/commit/eb5286902cccd3f889f9ed1d84dd4bbfdb459d6b))
* modernize plugin structure with skills, agents, hooks, and output styles ([b2490bc](https://github.com/athrael-soju/Larvling/commit/b2490bc44d39e7d76b525f2e3ca4cb50ddb16e0a))
* Refactor dashboard rendering logic and remove unused graph data handling ([6130f40](https://github.com/athrael-soju/Larvling/commit/6130f408528a8f8a39436fa48c8637a7db4abb3c))
* Remove auto-summarization script and update session end hook command ([52bc00d](https://github.com/athrael-soju/Larvling/commit/52bc00d51dff1e8116c2852fdabc62d02ccda866))
* Remove Fact Graph agent and related functionality from dashboard generation ([d77c11b](https://github.com/athrael-soju/Larvling/commit/d77c11bf2f91d814001c441485b6b1368b29e822))
* Update Larvling dashboard with Knowledge Graph and improve hooks ([26945f7](https://github.com/athrael-soju/Larvling/commit/26945f75562a8d5856f80c972291d6bea489dda8))
* Update README to reflect changes in command terminology and enhance dashboard description ([08d3614](https://github.com/athrael-soju/Larvling/commit/08d3614e9d1bac2726b8188be0ee5a5baa90886d))
* Update README to reflect on-demand dashboard generation and script modifications ([0a6c678](https://github.com/athrael-soju/Larvling/commit/0a6c6789996c43b15107e475af3e77439d0fe07a))
* Update terminology from 'commands' to 'skills' in documentation and precompact script ([47ef3f8](https://github.com/athrael-soju/Larvling/commit/47ef3f8dc8c921873740817841ef5e225bc84b7c))


### Bug Fixes

* add explicit AskUserQuestion usage to remember skill and fact-manager agent ([00553f6](https://github.com/athrael-soju/Larvling/commit/00553f6dde565cd50a107afa821092f330b74aca))
* make PostToolUseFailure hook synchronous for reliable stdin delivery ([199cb76](https://github.com/athrael-soju/Larvling/commit/199cb76ba7ce31d8163009fd921441f1e1b7aec5))

## [0.1.3](https://github.com/athrael-soju/Larvling/compare/v0.1.2...v0.1.3) (2026-02-23)


### Refactoring

* enhance fact handling instructions to improve consolidation and avoid duplicates ([79422f9](https://github.com/athrael-soju/Larvling/commit/79422f90826257dffa9d68ae0481c03b8654e81b))
* improve facts extraction process and clarify database querying instructions ([d11aa79](https://github.com/athrael-soju/Larvling/commit/d11aa7928031b8ff45bdab24059cbfd0e02feb0e))
* simplify facts schema and empower extraction agent ([041fb45](https://github.com/athrael-soju/Larvling/commit/041fb45b098e024369c05a89e66eec559dcc7b8d))
* refine simplified facts schema and further tune extraction agent behavior ([e89db5e](https://github.com/athrael-soju/Larvling/commit/e89db5e34621ef561263fb187954f0452cbdc4b7))
* streamline command documentation by removing example SQL queries and clarifying instructions ([6a78ef0](https://github.com/athrael-soju/Larvling/commit/6a78ef048afe7a24d46a8e965f199de5320149ef))

## [0.1.2](https://github.com/athrael-soju/Larvling/compare/v0.1.1...v0.1.2) (2026-02-23)


### Features

* add function to retrieve local time, UTC offset, and approximate location ([9856e9d](https://github.com/athrael-soju/Larvling/commit/9856e9d60e26b40fa120274ce3c3f2f10b6d1cec))
* refine extraction criteria for user facts to enhance relevance and quality ([b455635](https://github.com/athrael-soju/Larvling/commit/b455635b2ff786a4e023228baeba823b7fe3662c))


### Refactoring

* add type check for SDK call result and log unexpected types ([c43174e](https://github.com/athrael-soju/Larvling/commit/c43174eff68840b4cf4f5355d6f29bcf08cc1d9d))
* deduplicate shared helpers and tighten extraction ([2b71597](https://github.com/athrael-soju/Larvling/commit/2b7159756ee3a561abecd8ec386d053c7df5b858))
* deduplicate shared helpers and tighten extraction prompt ([cc58534](https://github.com/athrael-soju/Larvling/commit/cc5853414a97c430aac08daf045dad02ea0b8812))
* enhance command execution in hooks and improve extraction process ([fe1f841](https://github.com/athrael-soju/Larvling/commit/fe1f841c4a9b5e9e50d53ec3d4faf4fda2a80343))
* improve call_model function for better error handling and flexibility ([2c4a1f4](https://github.com/athrael-soju/Larvling/commit/2c4a1f440a0920a3879194e67ffaf0b549e0664e))
* improve error handling in call_model and clean up extraction logic ([11beca8](https://github.com/athrael-soju/Larvling/commit/11beca8b1a8e1b5891f3918c761400698acf7ae3))
* improve message parsing in call_model function for better error handling ([ed1822b](https://github.com/athrael-soju/Larvling/commit/ed1822bf2ba9f125ae56fa43ba8dd1c28a3de999))


### Documentation

* add alternative installation method for local plugin usage and database persistence ([d3d9628](https://github.com/athrael-soju/Larvling/commit/d3d96281c8a1cd264e2f09b7ef63ad2c79fd5cf8))

## [0.2.0](https://github.com/athrael-soju/Larvling/compare/v0.1.0...v0.2.0) (2026-02-21)


### Features

* add fact management docs, loops to status, expand to 109 tests ([ab91c8a](https://github.com/athrael-soju/Larvling/commit/ab91c8a7de7b956a177306390238991fdfe33847))
* add factcheck stop hook to enforce fact management before stopping ([8451e3d](https://github.com/athrael-soju/Larvling/commit/8451e3d17d3c30e389ca6522e2ddd0928ec5029c))
* add has_table function and enhance fact awareness in preflight and hooks ([8c099ed](https://github.com/athrael-soju/Larvling/commit/8c099ed91bd1e40718474cbd5d000169858315ad))
* enhance auto-summarization and topic extraction for improved session management ([2cc98b7](https://github.com/athrael-soju/Larvling/commit/2cc98b7c802927b1d9add93b40e06b59319480c8))
* enhance command execution in hooks with temporary file handling for improved data processing ([6625852](https://github.com/athrael-soju/Larvling/commit/6625852e0d41073b72e756ae7c54f6973ebe9c74))
* enhance documentation for fact management and add example SQL queries ([bb2ec10](https://github.com/athrael-soju/Larvling/commit/bb2ec10d3783c37b2e8a8c3fb4c2127e62bbd583))
* enhance fact check output with detailed instructions for management ([dca1791](https://github.com/athrael-soju/Larvling/commit/dca17914b6cb47293f339f98045075eb9ae946de))
* enhance fact management by introducing automatic fact extraction and refining hooks ([2e9c0e2](https://github.com/athrael-soju/Larvling/commit/2e9c0e2da169e9389493723255bc7e5f17cb9e72))
* enhance fact management by refining hooks for fact lookup and updates ([83ee096](https://github.com/athrael-soju/Larvling/commit/83ee0969c3630d0ecf100893d9473ea9dd69f812))
* enhance loop management with input validation and context building ([8551d6e](https://github.com/athrael-soju/Larvling/commit/8551d6e2eece6cb2afad81c4ee7220df840ca2c0))
* enhance stop handling with detailed iteration management instructions ([f1ccf0f](https://github.com/athrael-soju/Larvling/commit/f1ccf0fa8544896d2afa94e56c8de96d52323cdd))
* fix connection leak, improve session resolution, add 70-test suite ([17f7283](https://github.com/athrael-soju/Larvling/commit/17f728390a89bfc7b7905d10d4f089dea637c669))
* guarantee loop facts surface via source-based query ([ec3d2bc](https://github.com/athrael-soju/Larvling/commit/ec3d2bc60fb3d145163add017044bbfab9047d82))
* implement dynamic fact management system with new factcheck script ([03ef752](https://github.com/athrael-soju/Larvling/commit/03ef752692029ee0cfb1da604c88b53435a340d9))
* implement iteration loop functionality with start, cancel, and status commands ([e4d22a5](https://github.com/athrael-soju/Larvling/commit/e4d22a55ddb52d9d75a1ac9d7bdc72429a06de2d))
* implement marker file for fact queries in query script ([580c0aa](https://github.com/athrael-soju/Larvling/commit/580c0aa4b2ce75a4a5a19cf4d3a40b70c442af9a))
* implement unified data extraction and auto-summarization for session management ([548e95c](https://github.com/athrael-soju/Larvling/commit/548e95cd3ab69b47fbc714c2856051665b3083e9))
* streamline fact management by removing unused stop hook and updating command execution ([012e91c](https://github.com/athrael-soju/Larvling/commit/012e91c0aacf902172bb3af4ee98b969199e8e2f))
* update documentation to reflect unified extraction and dynamic topic consolidation ([ecd228b](https://github.com/athrael-soju/Larvling/commit/ecd228b938a66063dd622f1442976925e83b0ea1))
* update schemas to include topics and quality signals across commands and enhance logging for error handling ([06dbe15](https://github.com/athrael-soju/Larvling/commit/06dbe155765deadb23a4872f1746abdde6ccf385))


### Refactoring

* remove loop functionality and related documentation ([863a0ae](https://github.com/athrael-soju/Larvling/commit/863a0aeb577607295e14dec1b13b62f4159d7456))
* remove loop functionality and related documentation ([927c068](https://github.com/athrael-soju/Larvling/commit/927c068b99a0862412e69402c2d8042a206d232f))
* strip loops tab from dashboard (-10.4 KB) ([baac8c1](https://github.com/athrael-soju/Larvling/commit/baac8c1097dfbb8c05020ff34e12d523a1e9bb5a))


### Documentation

* add loop commands, loop.py to README ([da041ba](https://github.com/athrael-soju/Larvling/commit/da041ba7f6e995ea30043d93949be5da2556e75e))

## [0.1.0](https://github.com/athrael-soju/Larvling/compare/v0.0.1...v0.1.0) (2026-02-19)


### Features

* add .gitignore for __pycache__ and clean up stats computation by removing weekly activity tracking ([7946822](https://github.com/athrael-soju/Larvling/commit/7946822d888fbbafa2c903b29c47c67941d51898))
* Add bootstrap command documentation and enhance audit logging in preflight script ([d06bba1](https://github.com/athrael-soju/Larvling/commit/d06bba1338f9b2119724390dc4b310b91d9d7c1e))
* add changelog header with Larvling logo for improved presentation ([2270e5d](https://github.com/athrael-soju/Larvling/commit/2270e5d1bfe9a771dbf41cac7ca9e706bc599579))
* add changelog header with Larvling logo for improved presentation ([3d41478](https://github.com/athrael-soju/Larvling/commit/3d41478f3ecc321e12fc7e142860fe5c8606ba44))
* Add command descriptions and enhance dashboard functionality in documentation ([b878998](https://github.com/athrael-soju/Larvling/commit/b878998308717878d259ed0f7e43da0a13935919))
* add commands for remembering, recalling, and forgetting facts in Larvling ([b866848](https://github.com/athrael-soju/Larvling/commit/b8668481351b96c32256370a6060e05234b2a53e))
* Add database file and update .gitignore; refine session end and transcript logging ([0b7c151](https://github.com/athrael-soju/Larvling/commit/0b7c1512e7f64bbe8f65af0cf0be9c5a251fc453))
* Add export functionality for session conversations to markdown format ([4c7ad5b](https://github.com/athrael-soju/Larvling/commit/4c7ad5b53a61f28434c746383985c0948f1e2fed))
* Add first run welcome message and dashboard mention in CLAUDE.md and preflight.py ([5d87ce3](https://github.com/athrael-soju/Larvling/commit/5d87ce342ce659386c655bee9e6e649f6c3dc68d))
* Add initialization instructions for first-time users in CLAUDE.md ([12c718d](https://github.com/athrael-soju/Larvling/commit/12c718dc5678b8988190e41578fb82d13b053795))
* Add initialization message for first-time users in CLAUDE.md ([a2c835c](https://github.com/athrael-soju/Larvling/commit/a2c835cf7f7f4de3a32a6b317415728c60630f42))
* Add logo data URI generation for dynamic dashboard rendering ([3ad4fb7](https://github.com/athrael-soju/Larvling/commit/3ad4fb7bc05cfc86f8e3e2893a5d21d82e8c6e01))
* Add marketplace.json for plugin distribution ([e7fd6cf](https://github.com/athrael-soju/Larvling/commit/e7fd6cf588dc8a69f5b625b30aa64b8373912564))
* Add plugin manifest and lifecycle hooks for conversation tracking and dashboard generation ([b8d3e5c](https://github.com/athrael-soju/Larvling/commit/b8d3e5c8008cc0fea564171b4506b6e55ef01766))
* add release automation configuration and manifest files ([f5c3e78](https://github.com/athrael-soju/Larvling/commit/f5c3e78e189e15f3706a0d6fd94f2650b7d95052))
* add search functionality for session content ([850dfdf](https://github.com/athrael-soju/Larvling/commit/850dfdffeda6b1c779e705f866e7b33044229af8))
* Add session end handling and dashboard generation ([26ea1dc](https://github.com/athrael-soju/Larvling/commit/26ea1dc11deb16bc7bd0a7d3400931b96e649d3b))
* add stats command for aggregate session statistics ([47dffba](https://github.com/athrael-soju/Larvling/commit/47dffba6eea328208a17b46922fdc34a13862e98))
* add stats command for aggregate session statistics ([850dfdf](https://github.com/athrael-soju/Larvling/commit/850dfdffeda6b1c779e705f866e7b33044229af8))
* add update check for local plugin version against latest GitHub release ([347b9c9](https://github.com/athrael-soju/Larvling/commit/347b9c903abd39121a89a3a2eae5f1df932a6c93))
* add version display to dashboard and implement plugin version retrieval ([d8c3b7f](https://github.com/athrael-soju/Larvling/commit/d8c3b7ff06f7a094776881973c5b61c9fc024590))
* enhance command documentation and add new commands for session management ([3dbab2b](https://github.com/athrael-soju/Larvling/commit/3dbab2b2d2c3255e9210aee96fd4b292c7ce47f6))
* Enhance dashboard styling with new font and logo adjustments ([1431c7b](https://github.com/athrael-soju/Larvling/commit/1431c7b8032ac19431f5fdecf845025037d1d2d4))
* Enhance documentation for local development and update session management commands ([3f078c7](https://github.com/athrael-soju/Larvling/commit/3f078c763c3aa364cf1a25d1b7c170398bb42273))
* Enhance message body expansion with character count hint and improved styling ([18fead4](https://github.com/athrael-soju/Larvling/commit/18fead4ba7a4bde3ad2c1ea4ccedafe1085c1980))
* Enhance message body rendering with markdown support and improved truncation hints ([679a1e1](https://github.com/athrael-soju/Larvling/commit/679a1e1e1d9845be9a1f9f2243e10fe4c998074f))
* enhance topbar with info tooltip and link to GitHub repository ([825c979](https://github.com/athrael-soju/Larvling/commit/825c9794773372dbb90200a42843e5b09d61f0dc))
* Fix header formatting in README for consistency ([d10ce81](https://github.com/athrael-soju/Larvling/commit/d10ce81349e3505e47122de4204635ac55e0389c))
* implement memory management functionality with CRUD operations and update documentation ([a8ccd3f](https://github.com/athrael-soju/Larvling/commit/a8ccd3fd8203314fd452e3eabf1005d8af4a27e3))
* Implement session management features including delete, summarize, and export functionalities ([0839a24](https://github.com/athrael-soju/Larvling/commit/0839a243915458fb3b564541c0f9207604f7fb23))
* implement stats computation in stats.py ([850dfdf](https://github.com/athrael-soju/Larvling/commit/850dfdffeda6b1c779e705f866e7b33044229af8))
* introduce require_db function to streamline database existence checks across scripts ([a4469b5](https://github.com/athrael-soju/Larvling/commit/a4469b589ad40da36dd6820d45847d917461ceaa))
* Optimize session retrieval in dashboard and enhance session end logging ([9ca5534](https://github.com/athrael-soju/Larvling/commit/9ca553458b002d62a7dc321ece23af58804694a2))
* Refactor auditing to use imprints, remove obsolete files, and update documentation ([6291b54](https://github.com/athrael-soju/Larvling/commit/6291b544279dac836ec1d6fab6ae0f6c2b1477e9))
* Refactor database handling and improve session logging; update README and .gitignore ([f7f8576](https://github.com/athrael-soju/Larvling/commit/f7f857606a086bbb7f541da954dae8ee3a670993))
* Refactor session end handling by consolidating hooks and removing redundant script ([53ff998](https://github.com/athrael-soju/Larvling/commit/53ff9982ca8c881a38b2b7b9c4c33650def2a9f1))
* Simplify summarization process by removing scope selection and updating related instructions ([a219a13](https://github.com/athrael-soju/Larvling/commit/a219a13b83549a96adbd64498ed1a8606768595e))
* update command documentation and improve schema descriptions for clarity ([6af2f9d](https://github.com/athrael-soju/Larvling/commit/6af2f9db1a92844296d25f8b6072a3223ae1653c))
* Update first run messages to enhance user experience and provide clearer instructions ([e03d0a3](https://github.com/athrael-soju/Larvling/commit/e03d0a381c407e7cccf36e35bb5f48a412edfacc))
* Update installation and uninstallation instructions for clarity and consistency ([b378d4e](https://github.com/athrael-soju/Larvling/commit/b378d4eab0ee6482cfc32e60c803bd18db6a3238))
* Update logo handling to use remote URL instead of local file ([9052489](https://github.com/athrael-soju/Larvling/commit/905248931f7b3e0cc4b13e5142c81aefcb0ea82d))
* Update plugin name and format keywords for improved readability ([01c20ff](https://github.com/athrael-soju/Larvling/commit/01c20ff0c7a1954015b50fc167b9694883b70920))
* Update README to correct logo path and add uninstall instructions ([e60e760](https://github.com/athrael-soju/Larvling/commit/e60e7608fc2b05cbb681a032fe16e3d7b34d592e))
* Update uninstallation instructions to reflect correct marketplace source ([5170f23](https://github.com/athrael-soju/Larvling/commit/5170f23f4ad8a68f539bd676f845280985ea76cb))


### Bug Fixes

* Center logo image in README for improved presentation ([f9df8e4](https://github.com/athrael-soju/Larvling/commit/f9df8e4edf2fc77f1c68f68afe5279073f0d923b))
* correct version entry in plugin.json ([48c5590](https://github.com/athrael-soju/Larvling/commit/48c5590bc68c291d12c482a53ec175c1855ce590))
* enhance summarize command to handle multiple sessions ([850dfdf](https://github.com/athrael-soju/Larvling/commit/850dfdffeda6b1c779e705f866e7b33044229af8))
* Increase logo size in top bar for better visibility ([07edca1](https://github.com/athrael-soju/Larvling/commit/07edca11a0758deb73d1c13d74bfeb51b61bd7bf))
* increase timeout for dashboard command and improve error logging in hooks ([41dfb70](https://github.com/athrael-soju/Larvling/commit/41dfb70c3a9e4b057aa073219405aee09068e74b))
* remove stats functionality and related references from dashboard ([fb1fc0e](https://github.com/athrael-soju/Larvling/commit/fb1fc0ea1d05ee8514c4a7957cc73f2d8b35c637))
* resolve_session error messages, LIKE escaping, and misleading function name ([965337c](https://github.com/athrael-soju/Larvling/commit/965337cfb2f4ca270ce54e4c44b4ee7060a3f517))
* standardize descriptions of principles in README and dashboard template ([fe2a58b](https://github.com/athrael-soju/Larvling/commit/fe2a58b2d39cd8efcefcc18841dc54d2dbfc0baf))
* standardize hyphen usage in descriptions and comments across multiple files ([113da3b](https://github.com/athrael-soju/Larvling/commit/113da3b283fc19e0c7bd946f701677b60d67c618))
* Standardize punctuation in README for consistency ([c030247](https://github.com/athrael-soju/Larvling/commit/c030247a2d1338b949de1c0fe5b71a40870849ef))
* update dashboard to poll for new data every 3 seconds and reload automatically ([1c957e4](https://github.com/athrael-soju/Larvling/commit/1c957e4631531374a490b03d754309c35cda7735))
* update export button label from "Export conversation" to "Export session" ([8dd19aa](https://github.com/athrael-soju/Larvling/commit/8dd19aaa9905f0d8c7526b725210aaef85bc74b8))
* update export button label from "Export conversation" to "Export session" ([9b4426b](https://github.com/athrael-soju/Larvling/commit/9b4426b3f496c7140982bcc2ea31397f7498dfc6))
* update installation instructions in README for clarity and consistency ([f443499](https://github.com/athrael-soju/Larvling/commit/f443499adc64a3b30ed48d7d0a8eac31df1e3459))
* Update local development path in README for clarity ([e273b12](https://github.com/athrael-soju/Larvling/commit/e273b129312354158da6cc6b9403e4835f4cfe51))
* Update plugin source structure in marketplace.json for GitHub integration ([d8889e5](https://github.com/athrael-soju/Larvling/commit/d8889e5a2547a5139b550a18a993f5549d2bc475))
* update punctuation for clarity in README and CLAUDE documentation ([5a36f1a](https://github.com/athrael-soju/Larvling/commit/5a36f1a4963c1a97ff80d6a39dafc733c0be82a9))
* update README and dashboard to reflect accurate size limits; refactor session metadata handling in scripts ([0092c1d](https://github.com/athrael-soju/Larvling/commit/0092c1d6e547677b81625e0d12c97d571c94d699))
* update section title for clarity in README ([b3ae7bd](https://github.com/athrael-soju/Larvling/commit/b3ae7bd1c240b4c8787ebbf89d4b89d8098246f9))
* update size description in principles section of README ([3df61ff](https://github.com/athrael-soju/Larvling/commit/3df61ff896cbb9e7afed638bd76169ca138def6c))
* update version numbers in manifest and plugin files to 0.0.1 ([26c48c2](https://github.com/athrael-soju/Larvling/commit/26c48c2194f3976cc06cd1c49bb158bfc9a375f4))


### Refactoring

* add backup functionality during schema migration; improve migration error messaging ([0a2873c](https://github.com/athrael-soju/Larvling/commit/0a2873cf38c7a42e9517d564535199a81f891d1e))
* add guidance for generating session summaries; suggest timing for offers based on conversation flow ([8c8a5d6](https://github.com/athrael-soju/Larvling/commit/8c8a5d6ae57b2488ce9bbf10622e709585f55106))
* enhance database connection management and improve revision handling; update query ordering and escape functions ([1cb1160](https://github.com/athrael-soju/Larvling/commit/1cb1160bb1b1109575ce1ac5a76457085360ec00))
* enhance schema management functions and update preflight checks; streamline migration handling ([619e0d2](https://github.com/athrael-soju/Larvling/commit/619e0d2f2f23c4073d81e08d4fa0046d8f5b3edf))
* enhance session management by updating ensure_session to create or touch session rows ([7b16ca0](https://github.com/athrael-soju/Larvling/commit/7b16ca03fd7f077e2adfdc38c50303b6bb43e154))
* remove summaries from schema and update related functionali… ([94ccde2](https://github.com/athrael-soju/Larvling/commit/94ccde20b406c758b3b9ace7fcdd4476edbb2303))
* remove summaries from schema and update related functionalities ([24b2ea1](https://github.com/athrael-soju/Larvling/commit/24b2ea1bfc0bc2d70a3466e977e4860721100009))
* replace get_db() with open_db() for better resource management across scripts ([10e36bf](https://github.com/athrael-soju/Larvling/commit/10e36bff8b0362156761fcdbbc53ab573a110d17))
* replace list_sessions with print_sessions for consistency across scripts ([5c03fe8](https://github.com/athrael-soju/Larvling/commit/5c03fe8078a4f96884c4968a2d4f9a273c44596d))
* streamline command documentation for clarity and conciseness ([9d1c591](https://github.com/athrael-soju/Larvling/commit/9d1c591211dd78645ace9e370dba9ab36a9c52a6))
* streamline database interactions in various scripts ([850dfdf](https://github.com/athrael-soju/Larvling/commit/850dfdffeda6b1c779e705f866e7b33044229af8))
* update command documentation and remove obsolete scripts ([e934504](https://github.com/athrael-soju/Larvling/commit/e934504296dd748b97465a4ca930bdfabe143d45))
* update command documentation and version in plugin.json ([7ae44ef](https://github.com/athrael-soju/Larvling/commit/7ae44ef600ebdd83cb96d4e7ca255b3ca651959b))
* update documentation for first run and memory management; enhance schema migration handling and database interactions ([36f23d2](https://github.com/athrael-soju/Larvling/commit/36f23d275868ca8880b71beac7096c820c7d26c9))
* update memory management in documentation and hooks; streamline session context handling ([679862d](https://github.com/athrael-soju/Larvling/commit/679862dbec040c012b4b732c2107716879e495de))
* update README and scripts for clarity; enhance session end handling and schema migration instructions ([f808c15](https://github.com/athrael-soju/Larvling/commit/f808c15317b1cfdecb2b177e52a27c44e31df9d4))
* update schema version from 1 to 3 ([05317f3](https://github.com/athrael-soju/Larvling/commit/05317f3198c6ab156ad06e8e68703052a30f35ce))


### Documentation

* Add "Why Larvling?" section with selling points to README ([c27deaa](https://github.com/athrael-soju/Larvling/commit/c27deaadf575d350c734f325dc6ffc8e5c258e21))
* Update installation instructions and add usage examples in README ([56d454c](https://github.com/athrael-soju/Larvling/commit/56d454cc51d502252543ffa8e8e6e242ee60d9c6))
* update README to clarify size specifications for the plugin ([e6dc392](https://github.com/athrael-soju/Larvling/commit/e6dc39253145d823a36ece699eb7c87a1127f581))
