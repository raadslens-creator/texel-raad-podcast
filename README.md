# Texel Raadsvergadering Podcast

Automatische podcast van de Texel gemeenteraadsvergaderingen via RoyalCast.

## Hoe het werkt

1. GitHub Actions draait wekelijks (donderdag 06:00 - na de woensdagavond vergadering)
2. Het script checkt de RoyalCast API op nieuwe vergaderingen
3. Als er een nieuwe is, wordt de audio gedownload via yt-dlp
4. De MP3 wordt geüpload als GitHub Release
5. De RSS-feed wordt bijgewerkt
6. De RSS-feed is bereikbaar via GitHub Pages en werkt in elke podcastapp

## Setup (eenmalig)

### 1. Maak een nieuwe GitHub-repository aan
Naam bijvoorbeeld: `texel-raad-podcast`  
Zet hem op **Public** (nodig voor GitHub Pages)

### 2. Upload deze bestanden naar de repo

### 3. Zet GitHub Pages aan
Ga naar Settings > Pages > Source: `main` branch, map `/docs`

### 4. Voeg een GitHub Token toe als secret
Ga naar Settings > Secrets > Actions > New secret  
Naam: `GH_TOKEN`  
Waarde: een Personal Access Token met `repo` en `write:packages` rechten  
(aanmaken via github.com > Settings > Developer settings > Personal access tokens)

### 5. Klaar
De workflow draait elke donderdag automatisch. Je kunt hem ook handmatig starten via Actions > Run workflow.

## RSS-feed abonneren
Na de eerste run is je feed beschikbaar op:  
`https://{jouw-gebruikersnaam}.github.io/texel-raad-podcast/feed.xml`

Voer deze URL in bij elke podcastapp (Pocket Casts, Apple Podcasts, Overcast, etc.)
