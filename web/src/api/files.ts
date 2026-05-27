import { apiDelete, apiFetch, apiGet } from "@/api/client";

export type FileKind = "file" | "dir";

export interface TreeEntry {
  name: string;
  path: string;
  kind: FileKind;
  size: number | null;
}

export interface FileContent {
  path: string;
  encoding: "utf-8" | "base64";
  text: string;
}

export interface WriteFileInput {
  content: string;
  encoding?: "utf-8" | "base64";
}

const enc = encodeURIComponent;

export const listTree = (workspaceId: string, path = "/"): Promise<TreeEntry[]> =>
  apiGet<TreeEntry[]>(`/_gapt/api/workspaces/${workspaceId}/tree?path=${enc(path)}`);

export const readFile = (workspaceId: string, path: string): Promise<FileContent> =>
  apiGet<FileContent>(`/_gapt/api/workspaces/${workspaceId}/file?path=${enc(path)}`);

export const writeFile = (
  workspaceId: string,
  path: string,
  input: WriteFileInput,
): Promise<FileContent> =>
  apiFetch<FileContent>(`/_gapt/api/workspaces/${workspaceId}/file?path=${enc(path)}`, {
    method: "PUT",
    json: input,
  });

export const deleteFile = (workspaceId: string, path: string): Promise<void> =>
  apiDelete<void>(`/_gapt/api/workspaces/${workspaceId}/file?path=${enc(path)}`);
