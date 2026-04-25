"use client";

import { useEffect, useState } from "react";
import { getResume, uploadResume, deleteResume } from "@/lib/actions/resume";
import { Button } from "@/components/ui/Button";
import { Upload, Trash2 } from "lucide-react";

export function ResumeSettings() {
  const [filename, setFilename] = useState<string | null>(null);
  const [keywordCount, setKeywordCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadResume() {
      try {
        const resume = await getResume();
        if (resume) {
          setFilename(resume.filename);
          setKeywordCount(resume.keywords.length);
        }
      } catch (err) {
        console.error("Failed to load resume:", err);
      } finally {
        setLoading(false);
      }
    }

    loadResume();
  }, []);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    // Validate file type
    if (!file.name.endsWith(".tex")) {
      setError("Only .tex files are supported");
      return;
    }

    setUploading(true);
    setError(null);

    try {
      const content = await file.text();
      const result = await uploadResume({
        filename: file.name,
        content,
      });

      if (result.uploaded) {
        setFilename(result.filename);
        // Keywords will be extracted and updated after the server action
        // For now, set a loading state
        setKeywordCount(0);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to upload resume");
    } finally {
      setUploading(false);
      // Reset input
      e.target.value = "";
    }
  }

  async function handleDelete() {
    if (!filename) return;

    if (!confirm("Are you sure you want to delete your resume?")) return;

    try {
      await deleteResume();
      setFilename(null);
      setKeywordCount(0);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete resume");
    }
  }

  if (loading) {
    return <div className="text-muted text-sm">Loading resume...</div>;
  }

  return (
    <div className="rounded-lg border border-border bg-card p-6 space-y-4">
      <div>
        <h3 className="font-semibold mb-2">Resume for Job Fit Analysis</h3>
        <p className="text-sm text-muted">
          Upload your LaTeX resume to enable job fit analysis. We'll extract key skills and technologies from your resume.
        </p>
      </div>

      {error && (
        <div className="rounded p-3 bg-error-bg text-error text-sm">
          {error}
        </div>
      )}

      {filename ? (
        <div className="space-y-3 bg-border-soft rounded p-4">
          <div>
            <p className="text-sm font-medium">Uploaded resume</p>
            <p className="text-sm text-muted">{filename}</p>
            <p className="text-xs text-muted mt-1">
              {keywordCount > 0 ? `${keywordCount} keywords extracted` : "Keywords pending extraction"}
            </p>
          </div>
          <div className="flex gap-2">
            <label className="flex-1">
              <button
                className="w-full inline-flex items-center justify-center gap-2 rounded-full font-semibold px-5 py-2 bg-primary text-primary-contrast border border-primary hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50"
                disabled={uploading}
              >
                <Upload className="h-4 w-4" />
                Replace
              </button>
              <input
                type="file"
                accept=".tex"
                onChange={handleFileChange}
                disabled={uploading}
                className="hidden"
              />
            </label>
            <Button
              variant="danger-outline"
              size="sm"
              onClick={handleDelete}
              disabled={uploading}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
      ) : (
        <label className="block">
          <div className="border-2 border-dashed border-border rounded-lg p-6 text-center hover:border-primary transition-colors cursor-pointer">
            <Upload className="h-8 w-8 mx-auto mb-2 text-muted" />
            <p className="text-sm font-medium mb-1">Upload your resume</p>
            <p className="text-xs text-muted">Drag and drop or click to select a .tex file</p>
          </div>
          <input
            type="file"
            accept=".tex"
            onChange={handleFileChange}
            disabled={uploading}
            className="hidden"
          />
        </label>
      )}

      {uploading && (
        <div className="text-sm text-muted text-center py-2">
          Uploading and processing resume...
        </div>
      )}
    </div>
  );
}
