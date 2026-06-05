import React, { useState, useRef, useEffect, useMemo } from 'react';
import CloseButton from './CloseButton';

const ChannelNameModal = ({ position, channel, modules, onClose, onSave }) => {
  const [value, setValue] = useState('');
  const inputRef = useRef(null);
  const modalMouseDownTarget = useRef(null);

  useEffect(() => {
    setValue(channel?.display_name ?? '');
    setTimeout(() => inputRef.current?.focus(), 50);
  }, [position, channel?.display_name]);

  const autoName = useMemo(() => {
    if (!channel?.modules?.length) return 'WIP';
    const mod = modules?.[channel.modules[0].module_id];
    return mod?.name || 'WIP';
  }, [channel, modules]);

  if (position === null) return null;

  const hasCustomName = !!channel?.display_name;
  const isDirty = value.trim() !== (channel?.display_name ?? '');

  const handleSave = () => {
    onSave(value.trim() || null);
    onClose();
  };

  const handleReset = () => {
    onSave(null);
    onClose();
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSave();
    if (e.key === 'Escape') onClose();
  };

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
        className='border-4 rounded-xl p-4 sm:p-6 max-w-sm w-full shadow-lg'
        style={{ backgroundColor: 'var(--color-bg-card)', borderColor: 'var(--color-border-main)' }}
        onClick={(e) => e.stopPropagation()}>

        <div className='flex justify-between items-center mb-1'>
          <h3 className='text-xl font-bold text-black'>Channel {position} Name</h3>
          <CloseButton onClick={onClose} />
        </div>
        <p className='text-xs text-gray-500 mb-4'>Shown on the OLED display.</p>

        <input
          ref={inputRef}
          type='text'
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={autoName}
          maxLength={24}
          className='w-full border-2 rounded-lg px-3 py-2 text-sm font-bold focus:outline-none transition-colors'
          style={{ borderColor: 'var(--color-border-main)', backgroundColor: 'var(--color-bg-main)' }}
        />
        <p className='text-xs text-gray-400 mt-1 mb-4'>
          Leave empty to use automatic name: <span className='font-bold text-gray-500'>{autoName}</span>
        </p>

        <div className='flex gap-2'>
          <button
            type='button'
            onClick={handleSave}
            className='flex-1 py-2 rounded-lg text-sm font-bold transition-colors'
            style={{ backgroundColor: 'var(--color-brass)', color: 'white' }}>
            Save
          </button>
          {hasCustomName && (
            <button
              type='button'
              onClick={handleReset}
              className='flex-1 py-2 rounded-lg border-2 text-sm font-bold transition-colors hover:bg-gray-100'
              style={{ borderColor: 'var(--color-border-main)' }}>
              Reset to automatic
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default ChannelNameModal;
