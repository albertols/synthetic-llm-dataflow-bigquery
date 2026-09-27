--  Copyright 2026 The synthetic-llm-dataflow-bigquery Authors
--
--  Licensed under the Apache License, Version 2.0 (the "License");
--  you may not use this file except in compliance with the License.
--  You may obtain a copy of the License at
--
--      https://www.apache.org/licenses/LICENSE-2.0
--
--  Unless required by applicable law or agreed to in writing, software
--  distributed under the License is distributed on an "AS IS" BASIS,
--  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
--  See the License for the specific language governing permissions and
--  limitations under the License.
--
-- Convenience views over evaluation_data_history (ADR: registry is
-- append-only events, D7). Rendered by `schemas.view_sql(project, dataset)`,
-- which substitutes `{project}` and `{dataset}` and splits the statements
-- below on their trailing `;`.

-- The last event written per evaluation_id, RUNNING or FINAL.
CREATE OR REPLACE VIEW `{project}.{dataset}.evaluation_latest` AS
SELECT *
FROM `{project}.{dataset}.evaluation_data_history`
QUALIFY ROW_NUMBER() OVER (PARTITION BY evaluation_id ORDER BY recorded_at DESC) = 1;

-- The latest FINAL row per generation_job_id — one row per generation run,
-- once its evaluation has completed.
CREATE OR REPLACE VIEW `{project}.{dataset}.evaluation_latest_per_job` AS
SELECT *
FROM `{project}.{dataset}.evaluation_data_history`
WHERE event = 'FINAL'
QUALIFY ROW_NUMBER() OVER (PARTITION BY generation_job_id ORDER BY recorded_at DESC) = 1;
