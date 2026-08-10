/** Wire shapes the app reads. Mirrors `yeaboi/app/routes.py`. */

export interface User {
  id: string;
  email: string;
  name: string;
}

export type Role = 'owner' | 'editor' | 'viewer';

export interface ProjectSummary {
  id: string;
  name: string;
  role: Role;
  updated_at: number;
}

export interface Member {
  id: string;
  email: string;
  name: string;
  role: Role;
}

export interface ProjectDetail extends Omit<ProjectSummary, 'updated_at'> {
  created_at: number;
  updated_at: number;
  members: Member[];
}

export interface ArtifactSummary {
  id: string;
  kind: string;
  title: string;
  created_at: number;
}
