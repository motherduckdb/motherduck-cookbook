import { createRoot } from 'react-dom/client';
import MotherDuckDefault from './dives/motherduck-default';
import SwissMinimal from './dives/swiss-minimal';
import Paper from './dives/paper';
import Editorial from './dives/editorial';
import Terminal from './dives/terminal';

export const STYLES = [
	{ id: 'motherduck-default', label: 'MotherDuck default', Dive: MotherDuckDefault },
	{ id: 'swiss-minimal', label: 'Swiss minimal', Dive: SwissMinimal },
	{ id: 'paper', label: 'Paper', Dive: Paper },
	{ id: 'editorial', label: 'Editorial', Dive: Editorial },
	{ id: 'terminal', label: 'Terminal', Dive: Terminal },
];

const requested = new URLSearchParams(window.location.search).get('style');
const match = STYLES.find((style) => style.id === requested);
const root = createRoot(document.getElementById('root')!);

if (match) {
	root.render(<match.Dive />);
} else {
	root.render(
		<ul className="p-8 font-sans text-lg">
			{STYLES.map((style) => (
				<li key={style.id} className="py-1">
					<a className="underline" href={`/?style=${style.id}`}>
						{style.label}
					</a>
				</li>
			))}
		</ul>,
	);
}
