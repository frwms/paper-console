import React, { useRef } from 'react';
import CloseButton from './CloseButton';

const Toggle = ({ checked, onChange, disabled }) => (
  <button
    type='button'
    role='switch'
    aria-checked={checked}
    disabled={disabled}
    onClick={() => !disabled && onChange(!checked)}
    className={`relative inline-flex h-6 w-11 flex-shrink-0 rounded-full border-2 transition-colors duration-200 focus:outline-none ${
      disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'
    }`}
    style={
      checked && !disabled
        ? { backgroundColor: 'var(--color-brass)', borderColor: 'var(--color-brass)' }
        : { backgroundColor: 'transparent', borderColor: '#9ca3af' }
    }>
    <span
      className={`inline-block h-4 w-4 rounded-full transition-transform duration-200 mt-0.5 ${
        checked ? 'translate-x-5' : 'translate-x-0.5'
      }`}
      style={{ backgroundColor: checked && !disabled ? 'white' : '#9ca3af' }}
    />
  </button>
);

const SegmentedControl = ({ value, options, onChange, disabled }) => (
  <div className={`flex rounded-lg border-2 overflow-hidden ${disabled ? 'opacity-40' : ''}`}
       style={{ borderColor: disabled ? '#9ca3af' : 'var(--color-border-main)' }}>
    {options.map((opt) => {
      const active = value === opt.value;
      return (
        <button
          key={opt.value}
          type='button'
          disabled={disabled}
          onClick={() => !disabled && onChange(opt.value)}
          className={`flex-1 px-2 py-1 text-xs font-bold transition-colors ${
            disabled ? 'cursor-not-allowed' : 'cursor-pointer'
          }`}
          style={
            active && !disabled
              ? { backgroundColor: 'var(--color-brass)', color: 'white', borderColor: 'var(--color-brass)' }
              : { backgroundColor: 'transparent', color: disabled ? '#9ca3af' : 'var(--color-text-main)' }
          }>
          {opt.label}
        </button>
      );
    })}
  </div>
);

const Row = ({ label, description, disabled, children }) => (
  <div className={`flex items-start justify-between gap-4 ${disabled ? 'opacity-40' : ''}`}>
    <div className='flex-1'>
      <div className='text-sm font-bold text-black mb-0.5'>{label}</div>
      <div className='text-xs text-gray-500'>{description}</div>
    </div>
    <div className='pt-0.5 flex-shrink-0'>{children}</div>
  </div>
);

const DAY_FORMAT_OPTIONS = [
  { value: 'full', label: 'Full' },
  { value: 'short', label: 'Short' },
  { value: 'none', label: 'None' },
];

const ChannelSettingsModal = ({ position, channel, onClose, onUpdate }) => {
  const modalMouseDownTarget = useRef(null);

  if (position === null) return null;

  const onlyOneDate = channel?.only_one_date ?? false;
  const invertHeaderStyle = channel?.invert_header_style ?? true;
  const showTime = channel?.show_time ?? true;
  const dayFormat = channel?.day_format ?? 'full';

  const update = (patch) =>
    onUpdate({
      only_one_date: onlyOneDate,
      invert_header_style: invertHeaderStyle,
      show_time: showTime,
      day_format: dayFormat,
      ...patch,
    });

  return (
    <div
      className='fixed inset-0 bg-black/80 flex items-center justify-center z-50 p-4'
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) modalMouseDownTarget.current = 'backdrop';
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && modalMouseDownTarget.current === 'backdrop') onClose();
        modalMouseDownTarget.current = null;
      }}>
      <div
        className='border-4 rounded-xl p-4 sm:p-6 max-w-md w-full shadow-lg'
        style={{ backgroundColor: 'var(--color-bg-card)', borderColor: 'var(--color-border-main)' }}
        onClick={(e) => e.stopPropagation()}>
        <div className='flex justify-between items-center mb-6'>
          <h3 className='text-xl font-bold text-black'>Channel {position} Settings</h3>
          <CloseButton onClick={onClose} />
        </div>

        <div className='space-y-5'>
          <Row
            label='Single Date Header'
            description='Print the date and time once at the top of the channel, instead of repeating it inside each module.'>
            <Toggle
              checked={onlyOneDate}
              disabled={false}
              onChange={(val) => update({ only_one_date: val })}
            />
          </Row>

          <Row
            label='Date as Main Header'
            description='Make the date the prominent top-level heading and show module names as smaller subheadings below it.'
            disabled={!onlyOneDate}>
            <Toggle
              checked={invertHeaderStyle}
              disabled={!onlyOneDate}
              onChange={(val) => update({ invert_header_style: val })}
            />
          </Row>

          <Row
            label='Show Time'
            description='Include the time of printing in the date header.'
            disabled={!onlyOneDate}>
            <Toggle
              checked={showTime}
              disabled={!onlyOneDate}
              onChange={(val) => update({ show_time: val })}
            />
          </Row>

          <Row
            label='Day of Week'
            description='How to display the day name — Full ("Sunday"), Short ("Sun"), or hidden.'
            disabled={!onlyOneDate}>
            <SegmentedControl
              value={dayFormat}
              options={DAY_FORMAT_OPTIONS}
              disabled={!onlyOneDate}
              onChange={(val) => update({ day_format: val })}
            />
          </Row>
        </div>
      </div>
    </div>
  );
};

export default ChannelSettingsModal;
