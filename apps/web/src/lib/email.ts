import "server-only";
import { Resend } from "resend";
import type { Locale } from "@/lib/i18n";

const resend = new Resend(process.env.RESEND_API_KEY);

const FROM_ADDRESS = "Job Seek <noreply@updates.colophon-group.org>";

const copy = {
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
} as const satisfies Record<Locale, Record<string, string>>;

export async function sendVerificationEmail(
  to: string,
  url: string,
  locale: Locale = "en",
) {
  const t = copy[locale];

  await resend.emails.send({
    from: FROM_ADDRESS,
    to,
    subject: t.subject,
    html: `
      <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2>${t.heading}</h2>
        <p>${t.body}</p>
        <a href="${url}"
           style="display: inline-block; padding: 10px 20px; background: #111111; color: #f5f5f5; text-decoration: none; border-radius: 9999px; font-weight: 600; font-size: 16px;">
          ${t.button}
        </a>
        <p style="margin-top: 16px; color: #666; font-size: 14px;">
          ${t.fallback}<br/>
          <a href="${url}" style="color: #666; word-break: break-all;">${url}</a>
        </p>
        <p style="margin-top: 16px; color: #666; font-size: 14px;">
          ${t.ignore}
        </p>
      </div>
    `,
  });
}
