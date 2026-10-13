-- Tables attendues quand instance.json contient un bloc "supabase".
-- Colonnes et clé étrangère vérifiées sur une base en service ; les contraintes
-- unique sont recommandées (l'écran Établissements vérifie déjà les doublons).
-- Les colonnes lues sont celles du select de config._societes_distantes().

create table societes (
  id              bigint generated always as identity primary key,
  nom             text not null unique,
  siren           text not null default '',
  comptable_email text not null default '',
  mentions        jsonb not null default '{}',   -- RaisonSociale, FormeCapital, GreffeRCS, RCS…
  ordre           int  not null default 0
);

create table etablissements (
  id            bigint generated always as identity primary key,
  societe_id    bigint not null references societes(id),
  nom           text not null,
  siret         text not null default '',
  groupe        text not null default '',
  manager_email text not null default '',
  contrat       text not null default '',       -- '' ou 'genere' = généré ; 'depose' = déposé
  mentions      jsonb not null default '{}',    -- AdresseEtablissement…
  ordre         int  not null default 0,
  unique (societe_id, nom)
);
