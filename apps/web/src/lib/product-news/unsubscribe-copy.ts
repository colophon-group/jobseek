import type { Locale } from "@/lib/i18n";

// This route returns plain HTML, like the job-alert unsubscribe route. Keep all
// supported languages here; do not load React or account state into the page.
export const productNewsUnsubscribeCopy: Record<Locale, {
  confirm: string; explanation: string; unsubscribe: string; done: string;
  doneBody: string; invalid: string; settings: string; changed: string;
}> = {
  en: {
    confirm: "Unsubscribe from product news?",
    explanation: "Stop Job Seek product updates and offers. Your job alerts and account emails are unaffected.",
    unsubscribe: "Unsubscribe", done: "You’re unsubscribed", doneBody: "You will no longer receive Job Seek product news. Your job alerts and account emails are unaffected.",
    invalid: "This link is invalid. You can manage product news in Settings.", settings: "Product news settings",
    changed: "Your preference changed since this email was sent. Please manage your current choice in Settings.",
  },
  de: {
    confirm: "Produktneuigkeiten abbestellen?",
    explanation: "Keine Produktneuigkeiten und Angebote von Job Seek mehr erhalten. Jobbenachrichtigungen und Konto-E-Mails bleiben davon unberührt.",
    unsubscribe: "Abmelden", done: "Du bist abgemeldet", doneBody: "Du erhältst keine Produktneuigkeiten von Job Seek mehr. Jobbenachrichtigungen und Konto-E-Mails bleiben davon unberührt.",
    invalid: "Dieser Link ist ungültig. Du kannst Produktneuigkeiten in den Einstellungen verwalten.", settings: "Einstellungen für Produktneuigkeiten",
    changed: "Deine Auswahl hat sich seit dem Versand dieser E-Mail geändert. Bitte verwalte deine aktuelle Auswahl in den Einstellungen.",
  },
  fr: {
    confirm: "Se désinscrire des nouveautés ?",
    explanation: "Ne plus recevoir les nouveautés et offres de Job Seek. Vos alertes emploi et e-mails de compte ne sont pas concernés.",
    unsubscribe: "Se désinscrire", done: "Vous êtes désinscrit", doneBody: "Vous ne recevrez plus les nouveautés de Job Seek. Vos alertes emploi et e-mails de compte ne sont pas concernés.",
    invalid: "Ce lien n’est pas valide. Vous pouvez gérer les nouveautés dans les paramètres.", settings: "Paramètres des nouveautés",
    changed: "Votre choix a changé depuis l’envoi de cet e-mail. Gérez votre choix actuel dans les paramètres.",
  },
  it: {
    confirm: "Annullare l’iscrizione alle novità?",
    explanation: "Non ricevere più novità e offerte di Job Seek. Gli avvisi di lavoro e le email relative all’account non sono interessati.",
    unsubscribe: "Annulla iscrizione", done: "Iscrizione annullata", doneBody: "Non riceverai più le novità di Job Seek. Gli avvisi di lavoro e le email relative all’account non sono interessati.",
    invalid: "Questo link non è valido. Puoi gestire le novità nelle impostazioni.", settings: "Impostazioni delle novità",
    changed: "La tua scelta è cambiata dopo l’invio di questa email. Gestisci la scelta attuale nelle impostazioni.",
  },
};
