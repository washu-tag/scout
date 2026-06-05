<#import "template.ftl" as layout>
<@layout.emailLayout>
<p>User <strong>${username}</strong> has requested access to Scout<#if scoutSite??> (<strong>${scoutSite}</strong>)</#if>.</p>
<#if approvalUrl??>
<p>Please <a href="${approvalUrl}">review and approve their request</a>.</p>
<#else>
<p>Please <a href="${scoutUrl}">log in to Scout</a> to enable their account.</p>
</#if>
</@layout.emailLayout>