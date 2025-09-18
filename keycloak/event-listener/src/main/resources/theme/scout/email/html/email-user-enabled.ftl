<#import "template.ftl" as layout>
<@layout.emailLayout>
${kcSanitize(msg("emailUserEnabledBodyHtml", scoutUrl))?no_esc}
</@layout.emailLayout>