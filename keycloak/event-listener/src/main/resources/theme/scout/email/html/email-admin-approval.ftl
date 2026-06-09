<#import "template.ftl" as layout>
<@layout.emailLayout>
<p>User <strong>${username}</strong> has requested access to Scout<#if scoutSite??> (<strong>${scoutSite}</strong>)</#if>.</p>
<p>Please <a href="${approvalUrl}">review and approve their request</a>.</p>
</@layout.emailLayout>