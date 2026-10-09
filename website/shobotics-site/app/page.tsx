'use client';

import Image from 'next/image';
import { useEffect, useRef, useState } from 'react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { OrganicField, DomainGlyph, SignalRail } from '@/components/vector-art';

gsap.registerPlugin(ScrollTrigger);

const domains = [
  {
    number: '01',
    title: 'Mind',
    type: 'mind' as const,
    short: 'Exploring perception, cognition and the general interface between humans & machines.',
    long: 'We explore intelligence in all its forms — biological, cognitive and artificial. Our work lives in perception, attention, neural interfaces and the future of human-machine understanding.',
    cta: 'view mind projects'
  },
  {
    number: '02',
    title: 'Machine',
    type: 'machine' as const,
    short: 'Turning signals into embodied intelligence to build anything from sensors & robots to intelligence & cognition.',
    long: 'We build systems that sense, reason and act. From advanced sensing and imaging to robotics, AI architectures and embodied interfaces — we turn signals into intelligent action.',
    cta: 'view machine projects'
  },
  {
    number: '03',
    title: 'Media',
    type: 'media' as const,
    short: 'Working across visuals, sound, interaction & storytelling that promotes the culture of artistry.',
    long: 'We craft experiences that communicate, immerse and provoke. Through visuals, sound, interaction and performance, we design new media that shapes how stories are felt and remembered.',
    cta: 'view media projects'
  }
];

export default function Home() {
  const root = useRef<HTMLElement>(null);
  const [formState, setFormState] = useState<'idle' | 'sending' | 'sent' | 'error'>('idle');

  useEffect(() => {
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduceMotion || !root.current) return;

    const ctx = gsap.context(() => {
      gsap.from('.hero-logo', { autoAlpha: 0, y: 18, scale: 0.96, duration: 1.2, ease: 'power3.out' });
      gsap.from('.hero-kicker', { autoAlpha: 0, y: 16, duration: 0.9, delay: 0.45, ease: 'power3.out' });
      gsap.from('.hero-art', { autoAlpha: 0, scale: 1.04, duration: 1.6, delay: 0.25, ease: 'sine.out' });
      gsap.from('.geo', { autoAlpha: 0, scale: 0.88, stagger: 0.08, duration: 0.8, delay: 0.75, ease: 'power2.out' });

      gsap.to('.hero-logo-wrap', {
        scale: 0.33,
        xPercent: -36,
        yPercent: -45,
        transformOrigin: 'left center',
        ease: 'none',
        scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: 0.85 }
      });

      gsap.to('.hero-art', {
        yPercent: 36,
        xPercent: -8,
        scale: 0.72,
        opacity: 0.42,
        ease: 'none',
        scrollTrigger: { trigger: '.hero', start: 'top top', end: 'bottom top', scrub: 1 }
      });

      gsap.from('.mission-title span', {
        yPercent: 120,
        duration: 1,
        stagger: 0.05,
        ease: 'power3.out',
        scrollTrigger: { trigger: '.mission', start: 'top 62%' }
      });

      gsap.from('.domain-column', {
        autoAlpha: 0,
        y: 42,
        stagger: 0.16,
        duration: 1.05,
        ease: 'power3.out',
        scrollTrigger: { trigger: '.domain-grid', start: 'top 72%' }
      });

      gsap.from('.domain-card', {
        autoAlpha: 0,
        y: 58,
        stagger: 0.14,
        duration: 1.1,
        ease: 'expo.out',
        scrollTrigger: { trigger: '.project-cards', start: 'top 70%' }
      });
    }, root);

    return () => ctx.revert();
  }, []);

  async function submitContact(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormState('sending');
    const formData = new FormData(event.currentTarget);
    const payload = {
      name: formData.get('name'),
      email: formData.get('email'),
      description: formData.get('description')
    };

    const response = await fetch('/api/contact', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    setFormState(response.ok ? 'sent' : 'error');
    if (response.ok) event.currentTarget.reset();
  }

  return (
    <main ref={root}>
      <section className="hero panel" aria-label="Shobotics hero">
        <div className="hero-logo-wrap">
          <Image className="hero-logo" src="/shobotics-logo.jpeg" alt="Shobotics" width={444} height={165} priority />
          <p className="hero-kicker">Mind, Machine &amp; Media</p>
        </div>
        <div className="hero-art" aria-hidden="true">
          <OrganicField />
        </div>
        <div className="hero-caption">Architecting intelligent systems for strange, embodied futures.</div>
      </section>

      <section className="mission panel" aria-label="Mind Machine Media overview">
        <div className="mini-brand"><Image src="/shobotics-logo.jpeg" alt="Shobotics" width={120} height={45} /></div>
        <h1 className="mission-title"><span>Architecting</span> <span>intelligent</span> <span>systems</span> <span>for</span></h1>
        <div className="domain-grid">
          {domains.map((domain) => (
            <article className="domain-column" key={domain.title}>
              <h2>{domain.title === 'Media' ? '& Media' : domain.title}</h2>
              <DomainGlyph type={domain.type} compact />
              <p>{domain.short}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="projects panel" aria-label="Domains">
        <div className="project-cards">
          {domains.map((domain) => (
            <article className="domain-card" key={domain.number}>
              <div className="card-topline"><span>{domain.number}</span><SignalRail /></div>
              <h2>{domain.title}</h2>
              <DomainGlyph type={domain.type} />
              <p>{domain.long}</p>
              <a href="#contact">{domain.cta} <span>→</span></a>
            </article>
          ))}
        </div>
        <p className="projects-line">From insight to invention. From code to culture. From data to experience.</p>
      </section>

      <section className="contact panel" id="contact" aria-label="Contact Shobotics">
        <div className="contact-ghost"><OrganicField quiet /></div>
        <Image className="contact-logo" src="/shobotics-logo.jpeg" alt="Shobotics" width={120} height={45} />
        <h2>Open for Collaborations</h2>
        <div className="contact-layout">
          <div className="reach-list">
            <p className="accent">Reach out for:</p>
            {[
              ['technical consulting', 'systems architecture, imaging, AI, robotics, product prototyping'],
              ['creative technology', 'TouchDesigner, projection, interactive installations, generative visuals, stage systems'],
              ['research collaborations', 'embodied intelligence, perception, sensing, human-machine interaction'],
              ['venture conversations', 'partnerships, pilots, early-stage ideas and long-range bets']
            ].map(([title, text]) => (
              <div className="reach-item" key={title}><span>✣</span><div><strong>{title}</strong><p>{text}</p></div></div>
            ))}
          </div>
          <form className="contact-form" onSubmit={submitContact}>
            <label>Name<input name="name" required /></label>
            <label>Email ID<input name="email" type="email" required /></label>
            <label>Description<textarea name="description" required rows={5} /></label>
            <button disabled={formState === 'sending'}>{formState === 'sending' ? 'Sending…' : 'Send'}</button>
            {formState === 'sent' && <p className="form-note">Received. We’ll respond soon.</p>}
            {formState === 'error' && <p className="form-note error">Form endpoint not configured yet. Add GOOGLE_SCRIPT_URL in Vercel.</p>}
          </form>
        </div>
        <p className="bottom-line">Bring a problem, a prototype, a stage, a signal, or a <span>strange idea.</span></p>
      </section>
    </main>
  );
}
