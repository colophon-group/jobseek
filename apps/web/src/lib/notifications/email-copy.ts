/** Notification email/standalone unsubscribe copy (same locale strategy as auth email). */
export const notificationCopy = {
  en: {
    subject: "Your weekly job matches", heading: "New roles worth a look.",
    intro: "New openings from your enabled watchlists, together in one weekly email.",
    added: "Added {age}", matches: "Matching watchlists", more: "See all matches", settings: "Manage notifications",
    unsubscribe: "Pause all job emails", footer: "You receive this because you enabled weekly email notifications on a watchlist. Account emails are unaffected.",
    limited: "Showing up to 20 roles. Open your watchlists for all results.",
    confirm: "Pause weekly job emails?", explanation: "This pauses all watchlist notifications. Your watchlist choices are saved, and you can resume in Settings at any time.",
    done: "Job emails are paused", doneBody: "Your watchlist choices are unchanged. Resume in Settings when you are ready. Jobs from the paused period will not be emailed.",
    invalid: "This link is no longer valid. Please manage notifications in Settings.", role: "View role",
  },
  de: {
    subject: "Deine wöchentlichen Jobtreffer", heading: "Neue Stellen für dich.",
    intro: "Neue Stellen aus deinen aktivierten Watchlists, zusammen in einer wöchentlichen E-Mail.",
    added: "Hinzugefügt: {age}", matches: "Passende Watchlists", more: "Alle Treffer ansehen", settings: "Benachrichtigungen verwalten",
    unsubscribe: "Alle Job-E-Mails pausieren", footer: "Du erhältst diese E-Mail, weil du wöchentliche Benachrichtigungen für eine Watchlist aktiviert hast. Konto-E-Mails bleiben unverändert.",
    limited: "Bis zu 20 Stellen werden angezeigt. Öffne deine Watchlists für alle Ergebnisse.",
    confirm: "Wöchentliche Job-E-Mails pausieren?", explanation: "Alle Watchlist-Benachrichtigungen werden pausiert. Deine Auswahl bleibt gespeichert. Du kannst die Benachrichtigungen jederzeit in den Einstellungen fortsetzen.",
    done: "Job-E-Mails sind pausiert", doneBody: "Deine Watchlist-Auswahl bleibt unverändert. Du kannst Benachrichtigungen in den Einstellungen fortsetzen. Stellen aus der Pause werden nicht nachträglich versendet.",
    invalid: "Dieser Link ist nicht mehr gültig. Verwalte Benachrichtigungen bitte in den Einstellungen.", role: "Stelle ansehen",
  },
  fr: {
    subject: "Vos offres d’emploi de la semaine", heading: "De nouvelles opportunités pour vous.",
    intro: "Les nouvelles offres de vos listes activées, réunies dans un e-mail hebdomadaire.",
    added: "Ajoutée {age}", matches: "Listes correspondantes", more: "Voir toutes les offres", settings: "Gérer les notifications",
    unsubscribe: "Suspendre tous les e-mails d’emploi", footer: "Vous recevez cet e-mail car vous avez activé les notifications hebdomadaires d’une liste. Les e-mails liés à votre compte restent inchangés.",
    limited: "Jusqu’à 20 offres sont affichées. Ouvrez vos listes pour voir tous les résultats.",
    confirm: "Suspendre les e-mails d’emploi hebdomadaires ?", explanation: "Toutes les notifications de vos listes seront suspendues. Vos choix sont conservés et vous pouvez reprendre les envois dans les paramètres à tout moment.",
    done: "Les e-mails d’emploi sont suspendus", doneBody: "Vos choix de listes sont conservés. Reprenez les envois dans les paramètres quand vous le souhaitez. Les offres parues pendant la pause ne seront pas envoyées.",
    invalid: "Ce lien n’est plus valide. Veuillez gérer les notifications dans les paramètres.", role: "Voir l’offre",
  },
  it: {
    subject: "Le tue offerte di lavoro della settimana", heading: "Nuove opportunità per te.",
    intro: "Le nuove offerte delle tue liste attive, riunite in un’e-mail settimanale.",
    added: "Aggiunta {age}", matches: "Liste corrispondenti", more: "Vedi tutte le offerte", settings: "Gestisci le notifiche",
    unsubscribe: "Sospendi tutte le e-mail di lavoro", footer: "Ricevi questa e-mail perché hai attivato le notifiche settimanali per una lista. Le e-mail relative al tuo account restano invariate.",
    limited: "Sono mostrate fino a 20 offerte. Apri le tue liste per vedere tutti i risultati.",
    confirm: "Sospendere le e-mail settimanali di lavoro?", explanation: "Tutte le notifiche delle liste verranno sospese. Le tue scelte vengono conservate e puoi riprendere gli invii dalle impostazioni in qualsiasi momento.",
    done: "Le e-mail di lavoro sono sospese", doneBody: "Le tue scelte per le liste restano invariate. Riprendi gli invii dalle impostazioni quando vuoi. Le offerte pubblicate durante la pausa non verranno inviate.",
    invalid: "Questo link non è più valido. Gestisci le notifiche dalle impostazioni.", role: "Vedi l’offerta",
  },
} as const;
export type NotificationLocale = keyof typeof notificationCopy;
export function notificationLocale(value: string): NotificationLocale {
  return Object.hasOwn(notificationCopy, value) ? value as NotificationLocale : "en";
}
