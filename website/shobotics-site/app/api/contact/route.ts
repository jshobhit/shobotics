import { NextResponse } from 'next/server';

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const { name, email, description } = body || {};

    if (!name || !email || !description) {
      return NextResponse.json({ error: 'Missing required fields.' }, { status: 400 });
    }

    const scriptUrl = process.env.GOOGLE_SCRIPT_URL;
    if (!scriptUrl) {
      return NextResponse.json(
        { error: 'GOOGLE_SCRIPT_URL is not configured. Form UI works, but submissions are not connected yet.' },
        { status: 500 }
      );
    }

    const response = await fetch(scriptUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, email, description, source: 'shobotics-website', timestamp: new Date().toISOString() })
    });

    if (!response.ok) {
      return NextResponse.json({ error: 'Google Sheet endpoint rejected the submission.' }, { status: 502 });
    }

    return NextResponse.json({ ok: true });
  } catch (error) {
    return NextResponse.json({ error: 'Unable to submit contact form.' }, { status: 500 });
  }
}
