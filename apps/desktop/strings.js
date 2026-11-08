// Textes de l'application de bureau (le reste de l'interface vient du serveur, déjà traduit).
const FR = {
  title: 'Nexis',
  setupTitle: 'Votre serveur Nexis',
  setupLead: 'Adresse du serveur, telle que vous l’ouvrez dans un navigateur.',
  setupPlaceholder: 'https://nexis.mondomaine.fr ou 192.168.1.20:8420',
  connect: 'Se connecter',
  checking: 'Vérification…',
  notNexis: 'Aucun serveur Nexis ne répond à cette adresse.',
  unreachable: 'Serveur injoignable : {error}',
  offlineTitle: 'Serveur injoignable',
  offlineLead: 'Nexis n’a pas pu joindre {server}. Vérifiez la connexion, ou changez de serveur.',
  retry: 'Réessayer',
  changeServer: 'Changer de serveur…',
  updateReady: 'Une nouvelle version de Nexis est prête.',
  updateReadyDetail: 'Elle s’installe au redémarrage de l’application.',
  restart: 'Redémarrer',
  later: 'Plus tard',
  updateAvailable: 'Nexis {version} est disponible.',
  updateManual: 'Téléchargez-la depuis votre serveur, puis remplacez l’application.',
  download: 'Télécharger',
  upToDate: 'Nexis est à jour ({version}).',
  menuServer: 'Serveur',
  menuCheckUpdate: 'Rechercher une mise à jour',
  menuView: 'Affichage',
  menuEdit: 'Édition',
};
const EN = {
  title: 'Nexis',
  setupTitle: 'Your Nexis server',
  setupLead: 'The server address, as you open it in a browser.',
  setupPlaceholder: 'https://nexis.example.com or 192.168.1.20:8420',
  connect: 'Connect',
  checking: 'Checking…',
  notNexis: 'No Nexis server answers at this address.',
  unreachable: 'Server unreachable: {error}',
  offlineTitle: 'Server unreachable',
  offlineLead: 'Nexis could not reach {server}. Check the connection, or change server.',
  retry: 'Retry',
  changeServer: 'Change server…',
  updateReady: 'A new version of Nexis is ready.',
  updateReadyDetail: 'It installs when the app restarts.',
  restart: 'Restart',
  later: 'Later',
  updateAvailable: 'Nexis {version} is available.',
  updateManual: 'Download it from your server, then replace the app.',
  download: 'Download',
  upToDate: 'Nexis is up to date ({version}).',
  menuServer: 'Server',
  menuCheckUpdate: 'Check for updates',
  menuView: 'View',
  menuEdit: 'Edit',
};

function strings(locale) {
  return String(locale || '').toLowerCase().startsWith('fr') ? FR : EN;
}

function fill(text, vars) {
  return String(text).replace(/\{(\w+)\}/g, (_, k) => (vars && k in vars ? String(vars[k]) : `{${k}}`));
}

module.exports = { strings, fill };
