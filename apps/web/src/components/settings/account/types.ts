export type ConnectedAccount = {
  providerId: string;
  accountId: string;
};

export type AccountPageData = {
  accounts: ConnectedAccount[];
  hasPassword: boolean;
  username: string;
} | null;
