export type JobStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";

export interface Round {
  id: string;
  scene_id: string;
  version: number;
  status: string;
  subscenes: string[];
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SceneSummary {
  id: string;
  name: string;
  description: string;
  goal: string;
  owner: string;
  status: string;
  round: Round | null;
  material_count: number;
  rule_count: number;
  asset_count: number;
  created_at: string;
  updated_at: string;
}

export interface SceneDetail {
  id: string;
  name: string;
  description: string;
  goal: string;
  owner: string;
  status: string;
  rounds: Round[];
  created_at: string;
  updated_at: string;
}

export interface Material {
  id: string;
  round_id: string | null;
  exploration_id: string | null;
  name: string;
  role: string;
  extension: string;
  size_bytes: number;
  sha256: string;
  enabled: boolean;
  created_at: string;
}

export interface Job {
  id: string;
  kind: string;
  status: JobStatus;
  phase: string;
  progress: number;
  seq: number;
  message: string;
  scene_id: string | null;
  round_id: string | null;
  exploration_id: string | null;
  error: { code: string; message: string; retryable: boolean } | null;
  created_at: string;
  updated_at: string;
}

export interface JobEvent {
  seq: number;
  phase: string;
  status: JobStatus;
  progress: number;
  message: string;
}

export interface SourceRef {
  material_id: string;
  material_name: string;
  chunk_index: number;
  quote?: string;
}

export interface Rule {
  id: string;
  title: string;
  condition: string;
  action: string;
  exceptions: string;
  sources: SourceRef[];
}

export interface KnowledgeDocument {
  id: string;
  round_id: string;
  markdown: string;
  structured: {
    schema_version: string;
    scene: string;
    rules: Rule[];
    process: Array<{ step: number; name: string; description: string; sources: SourceRef[] }>;
    conflicts: string[];
    generated_by: string;
  };
  revision: number;
  updated_at: string;
}

export interface Suggestion {
  id: string;
  round_id: string;
  base_revision: number;
  old_text: string;
  new_text: string;
  explanation: string;
  source_refs: SourceRef[];
  status: string;
  created_at: string;
  resolved_at: string | null;
}

export interface Asset {
  id: string;
  round_id: string;
  kind: string;
  filename: string;
  mime_type: string;
  version: number;
  source_revision: number;
  stale: boolean;
  synthetic: boolean;
  size_bytes: number;
  created_at: string;
  download_url: string;
}

export interface ModelConnection {
  id: string;
  name: string;
  provider: string;
  api_base: string;
  model_name: string;
  enabled: boolean;
  has_api_key: boolean;
  created_at: string;
  updated_at: string;
}

export interface SkillVersion {
  id: string;
  name: string;
  description: string;
  version: string;
  status: string;
  built_in: boolean;
  kind: "TEMPLATE" | "INSTANCE";
  read_only: boolean;
  lineage_id: string;
  source_skill_id: string | null;
  source_name: string;
  scene_name: string;
  notes: string;
  manifest: Record<string, unknown>;
  created_at: string;
  download_url: string;
}

export interface AbilityParams {
  [key: string]: string | number | boolean | undefined;
  temperature?: number;
  max_chunks?: number;
  concurrency?: number;
  stability?: string;
  output_format?: string;
  few_shot_count?: number;
  package_format?: string;
  question_style?: string;
  density?: string;
  test_split?: number;
  boundary_coverage?: string;
}

export interface AbilityMount {
  id: string;
  profile_id: string | null;
  scope_key: string;
  inherited: boolean;
  ability_key: string;
  display_name: string;
  description: string;
  stage: "EXTRACTION" | "GENERATION";
  trigger: string;
  location: string;
  enabled: boolean;
  model_connection_id: string | null;
  skill_version_id: string | null;
  model: ModelConnection | null;
  skill: SkillVersion | null;
  params: AbilityParams;
  updated_at: string;
}

export interface AbilityScope {
  key: string;
  label: string;
  kind: "GLOBAL" | "SCENE" | "SUBSCENE";
}

export interface Candidate {
  id: string;
  name: string;
  description: string;
  goal: string;
  confidence: number;
  source_refs: SourceRef[];
  created_scene_id: string | null;
}

export interface Revision {
  id: string;
  revision: number;
  reason: string;
  author: string;
  created_at: string;
}
