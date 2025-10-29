<#import "template.ftl" as layout>
<@layout.emailLayout>
<p>User <strong>${username}</strong> has requested access to Scout. Please <a href="${scoutUrl}">log in to Scout</a> to enable their account.</p>
</@layout.emailLayout>