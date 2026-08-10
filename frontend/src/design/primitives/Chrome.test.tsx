/**
 * The milestone-2 chrome primitives.
 *
 * These are the first primitives in this vocabulary that carry *behaviour*, so
 * the tests are about wiring rather than markup: the label that is actually
 * associated with its control, the arrow keys that move between tabs, the live
 * region that exists before its message, the dialog that tells its owner when
 * Escape closed it. Every one of these is a thing that looks fine on screen and
 * is broken for someone using a keyboard or a screen reader.
 */

import { render, screen, waitFor } from '@testing-library/preact';
import userEvent from '@testing-library/user-event';
import { useState } from 'preact/hooks';
import { axe } from 'vitest-axe';
import { describe, expect, it, vi } from 'vitest';

import { Button } from './Button';
import { Field, Input } from './Field';
import { Modal } from './Modal';
import { Skeleton, SkeletonLines } from './Skeleton';
import { TabPanel, Tabs } from './Tabs';
import { ToastRegion, useToasts } from './Toast';

describe('<Button>', () => {
  it('defaults to type=button, not submit', async () => {
    // The HTML default submits the surrounding form, which is how a "Cancel"
    // next to an input silently starts saving.
    const onSubmit = vi.fn((e: Event) => e.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Button>Cancel</Button>
      </form>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('submits when asked to', async () => {
    const onSubmit = vi.fn((e: Event) => e.preventDefault());
    render(
      <form onSubmit={onSubmit}>
        <Button type="submit">Save</Button>
      </form>,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it('busy blocks activation and is announced, without changing the label', async () => {
    const onClick = vi.fn();
    render(
      <Button busy onClick={onClick}>
        Save
      </Button>,
    );
    const button = screen.getByRole('button', { name: 'Save' });
    expect(button.getAttribute('aria-busy')).toBe('true');
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('has no axe violations in any variant', async () => {
    const { container } = render(
      <>
        <Button variant="primary">One</Button>
        <Button variant="secondary">Two</Button>
        <Button variant="ghost">Three</Button>
      </>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('<Field>', () => {
  it('associates the label with the control', () => {
    render(
      <Field label="Email">{(props) => <Input {...props} type="email" />}</Field>,
    );
    // getByLabelText only finds it if htmlFor/id actually match.
    expect(screen.getByLabelText('Email')).toBeTruthy();
  });

  it('describes the control with the hint AND the error, not one or the other', () => {
    render(
      <Field label="Email" hint="Work address" error="Already taken">
        {(props) => <Input {...props} />}
      </Field>,
    );
    const described = screen.getByLabelText('Email').getAttribute('aria-describedby') ?? '';
    expect(described.split(' ')).toHaveLength(2);
    expect(screen.getByText('Work address').id).toBeTruthy();
    expect(screen.getByRole('alert').textContent).toBe('Already taken');
  });

  it('marks the control invalid only when there is an error', () => {
    const { rerender } = render(<Field label="Email">{(props) => <Input {...props} />}</Field>);
    expect(screen.getByLabelText('Email').getAttribute('aria-invalid')).toBeNull();
    rerender(
      <Field label="Email" error="Nope">
        {(props) => <Input {...props} />}
      </Field>,
    );
    expect(screen.getByLabelText('Email').getAttribute('aria-invalid')).toBe('true');
  });

  it('generates unique ids for two fields with the same label', () => {
    render(
      <>
        <Field label="Name">{(props) => <Input {...props} />}</Field>
        <Field label="Name">{(props) => <Input {...props} />}</Field>
      </>,
    );
    const [first, second] = screen.getAllByLabelText('Name');
    expect(first?.id).not.toBe(second?.id);
  });

  it('has no axe violations', async () => {
    const { container } = render(
      <Field label="Email" hint="Work address">
        {(props) => <Input {...props} type="email" />}
      </Field>,
    );
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('<Tabs>', () => {
  function Harness() {
    const [active, setActive] = useState('a');
    return (
      <>
        <Tabs
          label="Sections"
          active={active}
          onChange={setActive}
          items={[
            { id: 'a', label: 'Alpha' },
            { id: 'b', label: 'Beta' },
            { id: 'c', label: 'Gamma' },
          ]}
        />
        <TabPanel id="a" active={active}>
          Panel A
        </TabPanel>
        <TabPanel id="b" active={active}>
          Panel B
        </TabPanel>
      </>
    );
  }

  it('exposes exactly one selected tab', () => {
    render(<Harness />);
    expect(screen.getAllByRole('tab', { selected: true })).toHaveLength(1);
  });

  it('uses a roving tabindex so Tab crosses the strip in one press', () => {
    render(<Harness />);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.filter((tab) => tab.getAttribute('tabindex') === '0')).toHaveLength(1);
  });

  it('moves selection with the arrow keys, and wraps', async () => {
    render(<Harness />);
    const first = screen.getByRole('tab', { name: 'Alpha' });
    first.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Beta' }).getAttribute('aria-selected')).toBe('true');
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}');
    expect(screen.getByRole('tab', { name: 'Gamma' }).getAttribute('aria-selected')).toBe('true');
  });

  it('Home and End jump to the ends', async () => {
    render(<Harness />);
    screen.getByRole('tab', { name: 'Alpha' }).focus();
    await userEvent.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Gamma' }).getAttribute('aria-selected')).toBe('true');
    await userEvent.keyboard('{Home}');
    expect(screen.getByRole('tab', { name: 'Alpha' }).getAttribute('aria-selected')).toBe('true');
  });

  it('shows only the active panel', () => {
    render(<Harness />);
    expect(screen.getByRole('tabpanel').textContent).toBe('Panel A');
  });

  it('has no axe violations', async () => {
    const { container } = render(<Harness />);
    expect(await axe(container)).toHaveNoViolations();
  });
});

describe('<Modal>', () => {
  it('is not in the accessible tree when closed', () => {
    render(
      <Modal open={false} onClose={() => {}} title="Confirm">
        body
      </Modal>,
    );
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('names the dialog with its title', () => {
    render(
      <Modal open onClose={() => {}} title="Delete project">
        body
      </Modal>,
    );
    expect(screen.getByRole('dialog', { name: 'Delete project' })).toBeTruthy();
  });

  it('tells its owner when Escape closes it', async () => {
    // Without this the element shuts while `open` stays true, and the dialog
    // cannot be reopened until it is toggled twice.
    const onClose = vi.fn();
    render(
      <Modal open onClose={onClose} title="Confirm">
        body
      </Modal>,
    );
    screen.getByRole('dialog').dispatchEvent(new Event('cancel', { cancelable: true }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });
});

describe('<Skeleton>', () => {
  it('is hidden from assistive tech', () => {
    const { container } = render(<Skeleton />);
    expect(container.querySelector('[aria-hidden="true"]')).toBeTruthy();
  });

  it('renders one placeholder per line', () => {
    const { container } = render(<SkeletonLines count={4} />);
    expect(container.querySelectorAll('span > span')).toHaveLength(4);
  });
});

describe('toasts', () => {
  function Harness() {
    const { toasts, push, dismiss } = useToasts();
    return (
      <>
        <button type="button" onClick={() => push('Saved')}>
          fire
        </button>
        <ToastRegion toasts={toasts} onDismiss={dismiss} />
      </>
    );
  }

  it('renders the live region before any message exists', () => {
    // A live region inserted at the same moment as its content is not
    // announced by most screen readers.
    render(<Harness />);
    expect(screen.getByRole('status')).toBeTruthy();
  });

  it('shows a pushed message and dismisses it', async () => {
    render(<Harness />);
    await userEvent.click(screen.getByRole('button', { name: 'fire' }));
    expect(screen.getByText('Saved')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(screen.queryByText('Saved')).toBeNull());
  });
});
