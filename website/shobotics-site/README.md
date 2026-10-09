# Shobotics Living System Website

A Vercel-ready Next.js single-page site based on the Shobotics mockup: hero, Mind/Machine/Media system section, domain cards, and contact section.

## Run locally

```bash
npm install
npm run dev
```

Open `http://localhost:3000`.

## Google Sheet contact form setup

1. Create a Google Sheet with headers:
   `timestamp | name | email | description | source`
2. Go to `Extensions > Apps Script`.
3. Paste this script:

```js
function doPost(e) {
  const sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
  const data = JSON.parse(e.postData.contents);
  sheet.appendRow([
    data.timestamp || new Date().toISOString(),
    data.name || '',
    data.email || '',
    data.description || '',
    data.source || 'shobotics-website'
  ]);

  return ContentService
    .createTextOutput(JSON.stringify({ ok: true }))
    .setMimeType(ContentService.MimeType.JSON);
}
```

4. Deploy as `Web app`.
5. Set access to `Anyone`.
6. Copy the deployment URL.
7. In Vercel, add an environment variable:
   `GOOGLE_SCRIPT_URL=<your deployed Apps Script URL>`
8. Redeploy.

## Deploy to Vercel

```bash
npm install
npm run build
```

Push this folder to GitHub, import it into Vercel, add `GOOGLE_SCRIPT_URL`, then deploy.

## Wix integration options

### Recommended: link to the Vercel microsite
Use Wix as your main CMS/site shell, and link the Shobotics homepage button/menu to the Vercel URL.

### Embed inside Wix
Add a Wix Embed/iframe block and point it to the Vercel URL. Use a full-width section and set the iframe height to at least `3000px` or use Wix custom sizing. This keeps the high-end motion code outside Wix limitations.

## Asset swap points

- Replace `/public/shobotics-logo.jpeg` with a clean SVG later if available.
- The vector art is currently procedural inline SVG inside `components/vector-art.tsx`.
- Final custom line-art can be swapped into the same components while preserving animation hooks/classes.
