import "server-only";
import { Resend } from "resend";
import type { Locale } from "@/lib/i18n";

let _resend: Resend | null = null;
function getResend() {
  if (!_resend) _resend = new Resend(process.env.RESEND_API_KEY);
  return _resend;
}

const FROM_ADDRESS = "Job Seek <noreply@updates.colophon-group.org>";

type EmailCopy = Record<string, string>;

const verifyCopy = {
  en: {
    subject: "Verify your email address",
    heading: "Verify your email",
    body: "Click the button below to verify your email address.",
    button: "Verify email",
    fallback: "Or copy and paste this link into your browser:",
    ignore: "If you didn't create an account, you can safely ignore this email.",
  },
  de: {
    subject: "Bestätige deine E-Mail-Adresse",
    heading: "E-Mail bestätigen",
    body: "Klicke auf den Button, um deine E-Mail-Adresse zu bestätigen.",
    button: "E-Mail bestätigen",
    fallback: "Oder kopiere diesen Link in deinen Browser:",
    ignore: "Falls du kein Konto erstellt hast, kannst du diese E-Mail ignorieren.",
  },
  fr: {
    subject: "Vérifiez votre adresse e-mail",
    heading: "Vérifiez votre e-mail",
    body: "Cliquez sur le bouton ci-dessous pour vérifier votre adresse e-mail.",
    button: "Vérifier l'e-mail",
    fallback: "Ou copiez et collez ce lien dans votre navigateur :",
    ignore: "Si vous n'avez pas créé de compte, vous pouvez ignorer cet e-mail.",
  },
  it: {
    subject: "Verifica il tuo indirizzo email",
    heading: "Verifica la tua email",
    body: "Clicca il pulsante qui sotto per verificare il tuo indirizzo email.",
    button: "Verifica email",
    fallback: "Oppure copia e incolla questo link nel tuo browser:",
    ignore: "Se non hai creato un account, puoi ignorare questa email.",
  },
} as const satisfies Record<Locale, EmailCopy>;

const resetCopy = {
  en: {
    subject: "Reset your password",
    heading: "Reset your password",
    body: "Click the button below to set a new password.",
    button: "Reset password",
    fallback: "Or copy and paste this link into your browser:",
    ignore: "If you didn't request a password reset, you can safely ignore this email.",
  },
  de: {
    subject: "Passwort zurücksetzen",
    heading: "Passwort zurücksetzen",
    body: "Klicke auf den Button, um ein neues Passwort festzulegen.",
    button: "Passwort zurücksetzen",
    fallback: "Oder kopiere diesen Link in deinen Browser:",
    ignore: "Falls du kein Zurücksetzen angefordert hast, kannst du diese E-Mail ignorieren.",
  },
  fr: {
    subject: "Réinitialiser votre mot de passe",
    heading: "Réinitialiser votre mot de passe",
    body: "Cliquez sur le bouton ci-dessous pour définir un nouveau mot de passe.",
    button: "Réinitialiser le mot de passe",
    fallback: "Ou copiez et collez ce lien dans votre navigateur :",
    ignore: "Si vous n'avez pas demandé de réinitialisation, vous pouvez ignorer cet e-mail.",
  },
  it: {
    subject: "Reimposta la tua password",
    heading: "Reimposta la tua password",
    body: "Clicca il pulsante qui sotto per impostare una nuova password.",
    button: "Reimposta password",
    fallback: "Oppure copia e incolla questo link nel tuo browser:",
    ignore: "Se non hai richiesto il reset, puoi ignorare questa email.",
  },
} as const satisfies Record<Locale, EmailCopy>;

function escapeHtml(str: string) {
  return str.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildEmail(t: EmailCopy, url: string) {
  const safeUrl = escapeHtml(url);
  return `
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2>${t.heading}</h2>
      <p>${t.body}</p>
      <a href="${safeUrl}"
         style="display: inline-block; padding: 10px 20px; background: #111111; color: #f5f5f5; text-decoration: none; border-radius: 9999px; font-weight: 600; font-size: 16px;">
        ${t.button}
      </a>
      <p style="margin-top: 16px; color: #666; font-size: 14px;">
        ${t.fallback}<br/>
        <a href="${safeUrl}" style="color: #666; word-break: break-all;">${safeUrl}</a>
      </p>
      <p style="margin-top: 16px; color: #666; font-size: 14px;">
        ${t.ignore}
      </p>
    </div>
  `;
}

export async function sendVerificationEmail(
  to: string,
  url: string,
  locale: Locale = "en",
) {
  const t = verifyCopy[locale];
  await getResend().emails.send({
    from: FROM_ADDRESS,
    to,
    subject: t.subject,
    html: buildEmail(t, url),
  });
}

export async function sendResetPasswordEmail(
  to: string,
  url: string,
  locale: Locale = "en",
) {
  const t = resetCopy[locale];
  await getResend().emails.send({
    from: FROM_ADDRESS,
    to,
    subject: t.subject,
    html: buildEmail(t, url),
  });
}

const changeEmailCopy = {
  en: {
    subject: "Confirm your email change",
    heading: "Email change requested",
    button: "Confirm email change",
    fallback: "Or copy and paste this link into your browser:",
    ignore: "If you did not request this change, please ignore this email — your account has not been modified.",
  },
  de: {
    subject: "E-Mail-Änderung bestätigen",
    heading: "E-Mail-Änderung angefordert",
    button: "E-Mail-Änderung bestätigen",
    fallback: "Oder kopiere diesen Link in deinen Browser:",
    ignore: "Falls du diese Änderung nicht angefordert hast, ignoriere diese E-Mail — dein Konto wurde nicht geändert.",
  },
  fr: {
    subject: "Confirmez le changement d'e-mail",
    heading: "Changement d'e-mail demandé",
    button: "Confirmer le changement",
    fallback: "Ou copiez et collez ce lien dans votre navigateur :",
    ignore: "Si vous n'avez pas demandé cette modification, ignorez cet e-mail — votre compte n'a pas été modifié.",
  },
  it: {
    subject: "Conferma la modifica dell'email",
    heading: "Modifica email richiesta",
    button: "Conferma la modifica",
    fallback: "Oppure copia e incolla questo link nel tuo browser:",
    ignore: "Se non hai richiesto questa modifica, ignora questa email — il tuo account non è stato modificato.",
  },
} as const satisfies Record<Locale, Omit<EmailCopy, "body">>;

/**
 * Notification sent to the OLD email address when a user requests an email
 * change.  The recipient must click the link to confirm the change was
 * intentional.  Without this step an attacker who steals an active session
 * can silently redirect the account to a new address.
 */
export async function sendChangeEmailConfirmationEmail(
  oldEmail: string,
  newEmail: string,
  url: string,
  locale: Locale = "en",
) {
  const t = changeEmailCopy[locale];
  const safeNew = newEmail.replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const bodies: Record<Locale, string> = {
    en: `A request was made to change your Job Seek account email address to <strong>${safeNew}</strong>. Click the button below to confirm.`,
    de: `Es wurde eine Anfrage gestellt, deine Job-Seek-E-Mail-Adresse in <strong>${safeNew}</strong> zu ändern. Klicke auf den Button, um zu bestätigen.`,
    fr: `Une demande a été faite pour changer l'adresse e-mail de ton compte Job Seek en <strong>${safeNew}</strong>. Clique sur le bouton ci-dessous pour confirmer.`,
    it: `È stata inoltrata una richiesta per cambiare l'email del tuo account Job Seek in <strong>${safeNew}</strong>. Clicca il pulsante qui sotto per confermare.`,
  };
  const safeUrl = escapeHtml(url);
  const html = `
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
      <h2>${t.heading}</h2>
      <p>${bodies[locale]}</p>
      <a href="${safeUrl}"
         style="display: inline-block; padding: 10px 20px; background: #111111; color: #f5f5f5; text-decoration: none; border-radius: 9999px; font-weight: 600; font-size: 16px;">
        ${t.button}
      </a>
      <p style="margin-top: 16px; color: #666; font-size: 14px;">
        ${t.fallback}<br/>
        <a href="${safeUrl}" style="color: #666; word-break: break-all;">${safeUrl}</a>
      </p>
      <p style="margin-top: 16px; color: #666; font-size: 14px;">
        ${t.ignore}
      </p>
    </div>
  `;
  await getResend().emails.send({
    from: FROM_ADDRESS,
    to: oldEmail,
    subject: t.subject,
    html,
  });
}

