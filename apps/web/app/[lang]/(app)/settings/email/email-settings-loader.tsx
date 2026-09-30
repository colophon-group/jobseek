import { Trans } from "@lingui/react/macro";
import { getSession } from "@/lib/sessionCache";
import { getNotificationPreferences } from "@/lib/actions/notifications";
import { NotificationSettings } from "@/components/settings/NotificationSettings";
import { LoginPrompt } from "@/components/settings/account/LoginPrompt";

export async function EmailSettingsLoader({ locale }: { locale: string }) {
  // Resolve the dynamic session before fetching account-scoped notification data.
  const session = await getSession();
  const notifications = session
    ? await getNotificationPreferences(locale)
    : null;
  return (
    <div>
      <h2 className="mb-7 text-xl font-semibold">
        <Trans
          id="settings.nav.email"
          comment="Email notification settings navigation label"
        >
          Email
        </Trans>
      </h2>
      {session && notifications ? (
        <NotificationSettings
          paused={notifications.notificationsPaused}
          verified={session.user.emailVerified}
          watchlists={notifications.watchlists}
        />
      ) : (
        <LoginPrompt />
      )}
    </div>
  );
}
