// Reads the Deep Dives syllabus (config/curriculum.yml) and the plan's start date (config/plan.yml) at build time.
import fs from 'node:fs';
import path from 'node:path';
import { parse } from 'yaml';
import { todayKey } from './posts';

export interface CurriculumWeek {
	week: number;
	theme?: string;
	objectives?: string[];
	use_case?: string;
	review?: boolean;
	done?: string;
	start: Date;
}
export interface CurriculumModule {
	id: string;
	name: string;
	phase: string;
	note?: string;
	weeks: CurriculumWeek[];
}

const read = (file: string) => parse(fs.readFileSync(path.resolve(file), 'utf8'));

export function loadCurriculum() {
	const plan = read('config/plan.yml');
	const start = new Date(`${String(plan.start_date).slice(0, 10)}T00:00:00Z`);
	const weekStart = (n: number) => new Date(start.getTime() + (n - 1) * 7 * 86400000);
	const modules: CurriculumModule[] = read('config/curriculum.yml').modules.map((m: any) => ({
		...m,
		weeks: m.weeks.map((w: any) => ({ ...w, start: weekStart(w.week) })),
	}));
	const today = new Date(`${todayKey()}T00:00:00Z`);
	const currentWeek = Math.floor((today.getTime() - start.getTime()) / (7 * 86400000)) + 1;
	return { modules, currentWeek, weekStart };
}
