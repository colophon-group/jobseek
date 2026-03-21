export const APPLICATION_STATUSES = [
  "saved",
  "applied",
  "interviewing",
  "offered",
  "rejected",
] as const;

export type ApplicationStatus = (typeof APPLICATION_STATUSES)[number];

export const INTERVIEW_TYPES = [
  "interview",
  "phone_screen",
  "video_call",
  "technical",
  "coding",
  "system_design",
  "behavioral",
  "onsite",
  "panel",
  "hiring_manager",
  "other",
] as const;

export type InterviewType = (typeof INTERVIEW_TYPES)[number];

export type MyJobEntry = {
  id: string;
  savedAt: string;
  status: ApplicationStatus;
  statusChangedAt: string;
  appliedAt: string | null;
  interviewCount: number;
  posting: {
    id: string;
    title: string | null;
    sourceUrl: string;
    firstSeenAt: string;
    isActive: boolean;
    salaryMin: number | null;
    salaryMax: number | null;
    salaryCurrency: string | null;
    salaryPeriod: string | null;
  };
  company: {
    id: string;
    name: string;
    slug: string;
    icon: string | null;
  };
  salaryOverride: {
    min: number | null;
    max: number | null;
    currency: string | null;
    period: string | null;
  };
};

export type MyJobDetail = MyJobEntry & {
  interviews: InterviewEntry[];
  offeredAt: string | null;
  rejectedAt: string | null;
};

export type InterviewEntry = {
  id: string;
  round: number;
  type: InterviewType;
  scheduledAt: string | null;
  createdAt: string;
};
