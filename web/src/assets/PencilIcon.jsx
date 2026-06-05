import React from 'react';

const PencilIcon = ({ className, title = 'rename', ...props }) => (
  <svg className={className} height="16" width="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" {...props}>
    <title>{title}</title>
    <g fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
      <path d="M11.5 1.5l3 3-9 9H2.5v-3l9-9z" />
      <line x1="9.5" y1="3.5" x2="12.5" y2="6.5" />
    </g>
  </svg>
);

export default PencilIcon;
