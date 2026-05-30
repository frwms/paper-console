import React from 'react';

const EyeIcon = ({ className, title = 'preview', ...props }) => (
  <svg className={className} height="16" width="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" {...props}>
    <title>{title}</title>
    <g fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
      <path d="M1,8S3.636,3,8,3s7,5,7,5-2.636,5-7,5S1,8,1,8Z" />
      <circle cx="8" cy="8" r="2" />
    </g>
  </svg>
);

export default EyeIcon;
